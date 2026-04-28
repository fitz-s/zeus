"""Heavy runtime helpers extracted from cycle_runner.

The goal is to keep `cycle_runner.py` focused on orchestration while preserving
monkeypatch-based tests that patch symbols on the cycle_runner module. Every
function here receives a `deps` object, typically the cycle_runner module.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from src.config import get_mode
from src.contracts.decision_evidence import DecisionEvidence, EvidenceAsymmetryError
from src.engine.time_context import lead_hours_to_date_start, lead_hours_to_settlement_close
from src.state.lifecycle_manager import (
    enter_day0_window_runtime_state,
    initial_entry_runtime_state_for_order_status,
)


CANONICAL_STRATEGY_KEYS = {
    "settlement_capture",
    "shoulder_sell",
    "center_buy",
    "opening_inertia",
}


# T4.2-Phase1 2026-04-23 (D4 audit-only): exit triggers whose statistical
# burden (2 consecutive negative cycles, no FDR correction) is weaker than
# the entry-side burden (bootstrap CI + BH-FDR). DecisionEvidence symmetry
# audit fires on these and only these.
#
# Excluded triggers and their rationale:
# - SETTLEMENT_IMMINENT / WHALE_TOXICITY / MODEL_DIVERGENCE_PANIC /
#   FLASH_CRASH_PANIC / RED_FORCE_EXIT / VIG_EXTREME — force-majeure exits
#   driven by market-mechanics or risk-layer mandates, not statistical
#   inference. Symmetry with a statistical entry burden is not a coherent
#   question.
# - DAY0_OBSERVATION_REVERSAL — single-cycle observation-authority exit
#   fired when Day0 forward-edge drops below threshold while
#   day0_active=True. It does NOT use a consecutive_confirmations gate,
#   so the Phase1 weak-exit evidence template (sample_size=2,
#   consecutive_confirmations=2) would misrepresent its actual burden and
#   pollute the Phase1 audit_log_false_positive_rate metric. Phase3 may
#   introduce an observation-grade evidence variant; out of Phase1 scope.
_D4_ASYMMETRIC_EXIT_TRIGGERS = frozenset({
    "EDGE_REVERSAL",
    "BUY_NO_EDGE_EXIT",
    "BUY_NO_NEAR_EXIT",
})


def _resolve_strategy_key(decision) -> str:
    strategy_key = str(getattr(decision, "strategy_key", "") or "").strip()
    return strategy_key if strategy_key in CANONICAL_STRATEGY_KEYS else ""


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
    api_positions = chain_positions_from_api(clob.get_positions_from_api(), ChainPosition=deps.ChainPosition)
    if api_positions is None:
        raise RuntimeError("chain sync returned None — API call succeeded but returned no data")
    return deps.reconcile_with_chain(portfolio, api_positions, conn=conn), True


def cleanup_orphan_open_orders(portfolio, clob, *, deps, conn=None) -> int:
    """Cancel exchange orders that are not tracked locally.

    Triple-confirmation guard (#63):
      1. Order is NOT in local portfolio tracking (order_id / last_exit_order_id)
      2. Order is NOT in execution_fact (recent command log) within 2 hours
      3. Only then cancel — otherwise log warning and skip (quarantine)
    """
    if not hasattr(clob, "get_open_orders"):
        return 0

    tracked_order_ids = set()
    for pos in portfolio.positions:
        if pos.order_id:
            tracked_order_ids.add(pos.order_id)
        if pos.last_exit_order_id:
            tracked_order_ids.add(pos.last_exit_order_id)

    # Build set of recently-commanded order IDs from trade_decisions
    recent_order_ids: set[str] = set()
    if conn is not None:
        try:
            from src.state.db import _table_exists
            if _table_exists(conn, "trade_decisions"):
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
                rows = conn.execute(
                    "SELECT order_id FROM trade_decisions WHERE order_posted_at >= ? AND order_id IS NOT NULL AND order_id != ''",
                    (cutoff,),
                ).fetchall()
                recent_order_ids = {str(r[0]) for r in rows}
        except Exception as exc:
            deps.logger.warning("Could not query trade_decisions for orphan guard: %s", exc)

    cancelled = 0
    for order in clob.get_open_orders():
        order_id = extract_order_id(order)
        if not order_id or order_id in tracked_order_ids:
            continue
        # Quarantine guard: if order appears in recent trade_decisions, do NOT cancel
        if order_id in recent_order_ids:
            deps.logger.warning(
                "Orphan order %s found in recent execution_fact — quarantining instead of cancelling",
                order_id,
            )
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

    # P7: Live — wallet_balance is the PRIMARY bankroll source.
    # config_cap acts as an upper-bound safety cap only.
    try:
        balance = float(clob.get_balance())
    except Exception as exc:
        deps.logger.warning("Wallet balance fetch failed: %s", exc)
        return None, {
            "config_cap_usd": config_cap,
            "wallet_balance_usd": None,
            "dynamic_cap_usd": None,
            "entry_block_reason": "wallet_query_failed",
            "entry_bankroll_contract": "live_wallet_primary_capped_by_config",
            "bankroll_truth_source": "wallet_balance",
            "wallet_balance_used": True,
        }

    if balance <= 0.0:
        deps.logger.warning("Wallet balance $%.2f — blocking new entries.", balance)
        return None, {
            "config_cap_usd": config_cap,
            "wallet_balance_usd": balance,
            "dynamic_cap_usd": None,
            "entry_block_reason": "entry_bankroll_non_positive",
            "entry_bankroll_contract": "live_wallet_primary_capped_by_config",
            "bankroll_truth_source": "wallet_balance",
            "wallet_balance_used": True,
        }

    effective_bankroll = min(balance, config_cap)
    return max(0.0, effective_bankroll), {
        "config_cap_usd": config_cap,
        "wallet_balance_usd": balance,
        "dynamic_cap_usd": effective_bankroll,
        "entry_bankroll_contract": "live_wallet_primary_capped_by_config",
        "bankroll_truth_source": "wallet_balance",
        "wallet_balance_used": True,
    }


def materialize_position(candidate, decision, result, portfolio, city, mode, *, state: str, env: str, bankroll_at_entry=None, deps):
    # B097 [YELLOW / flag for §7c architect sign-off]: bankroll_at_entry
    # must be captured authoritatively at the point of entry. Falling back
    # to None (which previously propagated through to Position) corrupts
    # subsequent per-position P&L and size-reconstruction analytics. Reject
    # the materialization outright rather than synthesize a fake value.
    if bankroll_at_entry is None:
        raise ValueError(
            f"materialize_position: bankroll_at_entry is None for trade_id={getattr(result, 'trade_id', '?')!r} "
            f"state={state!r} env={env!r}; entry materialization requires an authoritative bankroll snapshot"
        )
    now = deps._utcnow()
    entry_price = result.fill_price or result.submitted_price or decision.edge.entry_price
    shares = result.shares or (decision.size_usd / entry_price if entry_price > 0 else 0.0)
    timeout_at = ""
    if result.timeout_seconds:
        timeout_at = (now + timedelta(seconds=result.timeout_seconds)).isoformat()
    edge_source = decision.edge_source or deps._classify_edge_source(mode, decision.edge)
    strategy_key = _resolve_strategy_key(decision)
    if not strategy_key:
        raise ValueError("missing or invalid strategy_key on decision")

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
        bankroll_at_entry=bankroll_at_entry,
        entered_at=now.isoformat() if state == "entered" else "",
        entry_ci_width=max(0.0, decision.edge.ci_upper - decision.edge.ci_lower),
        unit=city.settlement_unit,
        token_id=decision.tokens["token_id"],
        no_token_id=decision.tokens["no_token_id"],
        strategy_key=strategy_key,
        strategy=strategy_key,
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
        env=env,
        # Slice P2-fix3 (post-review C1 from critic, 2026-04-26): drop the
        # redundant `getattr(..., "high")` fallback. MarketCandidate is a
        # dataclass with `temperature_metric: str = "high"` default at
        # evaluator.py:91, so candidate.temperature_metric is always set
        # for valid MarketCandidate instances. Pre-fix the getattr fallback
        # silently HIGH-stamped non-MarketCandidate-shaped inputs (e.g.,
        # custom test fixtures, dynamic dicts) onto Position, after which
        # resolve_position_metric would falsely classify them VERIFIED.
        # Now: AttributeError raises if a non-MarketCandidate flows here.
        temperature_metric=candidate.temperature_metric,
        entry_model_agreement=getattr(decision, "agreement", "NOT_CHECKED"),
    )


def _emit_day0_window_entered_canonical_if_available(
    conn,
    pos,
    *,
    day0_entered_at: str,
    previous_phase: str,
    deps,
) -> bool:
    """Day0-canonical-event feature slice (2026-04-24): emit canonical
    DAY0_WINDOW_ENTERED event after a successful day0 transition
    (post-memory-mutation, post-update_trade_lifecycle persist).

    Pre-this-slice: cycle_runtime set pos.state='day0_window' + persisted
    via update_trade_lifecycle but never wrote a canonical position_events
    record. This helper lands one via build_day0_window_entered_canonical
    _write + append_many_and_project. Clears T1.c-followup L875 OBSOLETE_
    PENDING_FEATURE (test_day0_transition_emits_durable_lifecycle_event).

    Returns True on successful write, False on non-fatal skip (conn None
    or RuntimeError from canonical transaction schema absence — matches
    the pattern from _dual_write_canonical_entry_if_available).
    """
    if conn is None:
        return False

    from src.engine.lifecycle_events import build_day0_window_entered_canonical_write
    from src.state.db import append_many_and_project

    try:
        # Query next sequence_no for this position (same pattern as
        # fill_tracker._mark_entry_filled at src/execution/fill_tracker.py:156).
        # Position may already have POSITION_OPEN_INTENT / ENTRY_ORDER_POSTED /
        # ENTRY_ORDER_FILLED events (sequence_no 1-3); day0 event takes 4+.
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
            (getattr(pos, "trade_id", ""),),
        ).fetchone()
        next_seq = int((row[0] if row else 0) or 0) + 1
        events, projection = build_day0_window_entered_canonical_write(
            pos,
            day0_entered_at=day0_entered_at,
            sequence_no=next_seq,
            previous_phase=previous_phase,
            source_module="src.engine.cycle_runtime",
        )
        append_many_and_project(conn, events, projection)
    except RuntimeError as exc:
        deps.logger.warning(
            "CANONICAL_DAY0_EMIT_SKIPPED trade_id=%s reason=%s",
            pos.trade_id,
            exc,
        )
        return False

    return True


def _dual_write_canonical_entry_if_available(
    conn,
    pos,
    *,
    decision_id: str | None,
    deps,
    decision_evidence: DecisionEvidence | None = None,
) -> bool:
    # T4.1b 2026-04-23 (D4 Option E): `decision_evidence` threads through
    # to `build_entry_canonical_write` so the ENTRY_ORDER_POSTED payload
    # carries the `decision_evidence_envelope` sidecar for T4.2-Phase1
    # exit-side read-back via `json_extract(payload_json,
    # '$.decision_evidence_envelope')`. Remains None on paths that do not
    # originate from an accept-path `EdgeDecision` (e.g. test harnesses);
    # the payload simply omits the key, preserving pre-slice wire format.
    if conn is None:
        return False

    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project

    try:
        events, projection = build_entry_canonical_write(
            pos,
            decision_id=decision_id,
            source_module="src.engine.cycle_runtime",
            decision_evidence=decision_evidence,
        )
        append_many_and_project(conn, events, projection)
    except RuntimeError as exc:
        deps.logger.warning("CANONICAL_DUAL_WRITE_SKIPPED trade_id=%s reason=%s", pos.trade_id, exc)
        return False

    return True


def reconcile_pending_positions(portfolio, clob, tracker, *, deps):
    summary = {"entered": 0, "voided": 0, "dirty": False, "tracker_dirty": False}
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


def _apply_acknowledged_quarantine_clears(portfolio, summary: dict, *, deps, conn=None) -> bool:
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
        result = deps.record_token_suppression(
            conn,
            token_id=token_id,
            condition_id=getattr(pos, "condition_id", ""),
            suppression_reason="operator_quarantine_clear",
            source_module="src.engine.cycle_runtime",
            evidence={"trade_id": getattr(pos, "trade_id", "")},
        )
        if result.get("status") != "written":
            summary["operator_clears_suppression_failed"] = (
                summary.get("operator_clears_suppression_failed", 0) + 1
            )
            deps.logger.warning(
                "Quarantine clear for %s was acknowledged but token suppression was not persisted: %s",
                token_id,
                result,
            )
            continue
        portfolio.ignored_tokens.append(token_id)
        summary["operator_clears_applied"] = summary.get("operator_clears_applied", 0) + 1
        portfolio_dirty = True
    return portfolio_dirty


def _position_state_value(pos) -> str:
    state = getattr(pos, "state", "")
    return getattr(state, "value", state) or ""


def _build_exit_context(
    pos,
    edge_ctx,
    *,
    hours_to_settlement,
    ExitContext,
    portfolio=None,
):
    if False:
        _ = pos.entry_method
        _ = pos.selected_method
    p_market = None
    if getattr(edge_ctx, "p_market", None) is not None and len(edge_ctx.p_market) > 0:
        # Bug #64: edge_ctx.p_market from monitor_refresh is single-element
        # [held_bin_price], so index 0 is correct here. The held_bin_index
        # routing happens in monitor_refresh._build_all_bins.
        p_market = float(edge_ctx.p_market[0])
    elif getattr(pos, "last_monitor_market_price", None) is not None:
        p_market = float(pos.last_monitor_market_price)

    best_bid = getattr(pos, "last_monitor_best_bid", None)

    position_state = _position_state_value(pos)

    # T6.4-phase2 (2026-04-24): thread portfolio context so
    # HoldValue.compute_with_exit_costs can compute correlation-crowding
    # cost over other held positions. Exclude self from the tuple; each
    # element is (cluster, size_usd, trade_id). When portfolio is None,
    # falls back to empty tuple / None bankroll — the downstream seam
    # treats that as "no co-held positions, correlation_crowding=0".
    portfolio_positions: tuple = ()
    bankroll = None
    if portfolio is not None:
        try:
            bankroll = float(getattr(portfolio, "bankroll", None) or 0.0) or None
        except (TypeError, ValueError):
            bankroll = None
        others = getattr(portfolio, "positions", None) or ()
        portfolio_positions = tuple(
            (str(p.cluster), float(p.size_usd), str(p.trade_id))
            for p in others
            if getattr(p, "trade_id", None) != getattr(pos, "trade_id", None)
        )

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
        chain_is_fresh=pos.chain_state == "synced",
        divergence_score=float(getattr(edge_ctx, "divergence_score", 0.0) or 0.0),
        market_velocity_1h=float(getattr(edge_ctx, "market_velocity_1h", 0.0) or 0.0),
        portfolio_positions=portfolio_positions,
        bankroll=bankroll,
    )


def _execution_stub(candidate, decision, result, city, mode, *, deps):
    edge_source = decision.edge_source or deps._classify_edge_source(mode, decision.edge)
    strategy_key = _resolve_strategy_key(decision)
    return SimpleNamespace(
        trade_id=result.trade_id,
        market_id=decision.tokens["market_id"],
        city=city.name,
        target_date=candidate.target_date,
        bin_label=decision.edge.bin.label,
        direction=decision.edge.direction,
        strategy_key=strategy_key,
        strategy=strategy_key,
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
    from src.execution.exit_lifecycle import (
        ExitContext,
        build_exit_intent,
        check_pending_exits,
        check_pending_retries,
        execute_exit,
        handle_exit_pending_missing,
        is_exit_cooldown_active,
    )
    from src.state.chain_reconciliation import quarantine_resolution_reason

    portfolio_dirty = _apply_acknowledged_quarantine_clears(
        portfolio,
        summary,
        deps=deps,
        conn=conn,
    )
    tracker_dirty = False

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
        pending_exit_resolution = handle_exit_pending_missing(portfolio, pos)
        if pending_exit_resolution["action"] == "closed":
            closed = pending_exit_resolution["position"]
            if closed is not None:
                tracker.record_exit(closed)
                tracker_dirty = True
                portfolio_dirty = True
                summary["exit_chain_missing_closed"] = summary.get("exit_chain_missing_closed", 0) + 1
            continue
        if pending_exit_resolution["action"] == "skip":
            summary["monitor_skipped_exit_pending_missing"] = summary.get("monitor_skipped_exit_pending_missing", 0) + 1
            continue
        if pos.state == "economically_closed":
            summary["monitor_skipped_economic_close"] = summary.get("monitor_skipped_economic_close", 0) + 1
            continue
        if pos.state == "admin_closed":
            summary["monitor_skipped_admin_close"] = summary.get("monitor_skipped_admin_close", 0) + 1
            continue
        if pos.state == "pending_exit":
            if pos.exit_state == "backoff_exhausted":
                summary["monitor_skipped_pending_exit_phase"] = summary.get("monitor_skipped_pending_exit_phase", 0) + 1
                continue
            if is_exit_cooldown_active(pos):
                summary["monitor_skipped_pending_exit_phase"] = summary.get("monitor_skipped_pending_exit_phase", 0) + 1
                continue
            check_pending_retries(pos, conn=conn)
            if pos.state == "pending_exit":
                summary["monitor_skipped_pending_exit_phase"] = summary.get("monitor_skipped_pending_exit_phase", 0) + 1
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

        # K1/#49: belt-and-suspenders guard — quarantine placeholders must not
        # reach monitor_refresh where cities_by_name lookup would fail.
        if getattr(pos, 'is_quarantine_placeholder', False):
            logger.warning("Quarantine placeholder %s reached monitor loop — skipping", pos.trade_id)
            summary["monitor_skipped_quarantine_placeholder"] = summary.get("monitor_skipped_quarantine_placeholder", 0) + 1
            continue

        hours_to_settlement = None
        monitor_result_written = False
        try:
            city = deps.cities_by_name.get(pos.city)
            if city is not None:
                hours_to_settlement = lead_hours_to_settlement_close(
                    pos.target_date,
                    city.timezone,
                    deps._utcnow(),
                )
                if (hours_to_settlement <= 6.0
                        and pos.state in {"entered", "holding"}
                        and not getattr(pos, "exit_state", "")):
                    new_state = enter_day0_window_runtime_state(
                        pos.state,
                        exit_state=getattr(pos, "exit_state", ""),
                        chain_state=getattr(pos, "chain_state", ""),
                    )
                    new_day0_entered_at = pos.day0_entered_at or deps._utcnow().isoformat()
                    # Day0-canonical-event slice 2026-04-24: capture
                    # pre-transition phase so the canonical event records
                    # the actual lifecycle transition (not just "from
                    # active" default).
                    previous_phase_str = "active" if pos.state == "holding" else "active"
                    # Persist FIRST, then update memory (avoid split-brain)
                    if conn is not None:
                        try:
                            from src.state.db import update_trade_lifecycle
                            # Temporarily set fields for persistence
                            old_state = pos.state
                            old_day0 = pos.day0_entered_at
                            pos.state = new_state
                            pos.day0_entered_at = new_day0_entered_at
                            update_trade_lifecycle(conn=conn, pos=pos)
                        except Exception as exc:
                            # Revert memory to pre-transition state
                            pos.state = old_state
                            pos.day0_entered_at = old_day0
                            deps.logger.warning(
                                "Day0 transition ABORTED for %s: persist failed: %s",
                                pos.trade_id,
                                exc,
                            )
                            continue
                    else:
                        pos.state = new_state
                        pos.day0_entered_at = new_day0_entered_at
                    portfolio_dirty = True
                    # Day0-canonical-event slice 2026-04-24: emit typed
                    # DAY0_WINDOW_ENTERED event post-transition. Clears
                    # T1.c-followup L875 OBSOLETE_PENDING_FEATURE.
                    # Non-fatal: if canonical schema absent or write fails,
                    # logs warning but does not abort the cycle.
                    _emit_day0_window_entered_canonical_if_available(
                        conn,
                        pos,
                        day0_entered_at=new_day0_entered_at,
                        previous_phase=previous_phase_str,
                        deps=deps,
                    )

            edge_ctx = refresh_position(conn, clob, pos)
            exit_context = _build_exit_context(
                pos,
                edge_ctx,
                hours_to_settlement=hours_to_settlement,
                ExitContext=ExitContext,
                portfolio=portfolio,
            )
            p_market = exit_context.current_market_price
            portfolio_dirty = True
            exit_decision = pos.evaluate_exit(exit_context)
            should_exit = exit_decision.should_exit
            exit_reason = exit_decision.reason
            if exit_reason.startswith("INCOMPLETE_EXIT_CONTEXT"):
                summary["monitor_incomplete_exit_context"] = summary.get("monitor_incomplete_exit_context", 0) + 1
                if hours_to_settlement is not None and hours_to_settlement <= 6.0:
                    summary["monitor_chain_missing"] = summary.get("monitor_chain_missing", 0) + 1
                    summary.setdefault("monitor_chain_missing_positions", []).append(pos.trade_id)
                    summary.setdefault("monitor_chain_missing_reasons", []).append(
                        {
                            "position_id": pos.trade_id,
                            "reason": f"incomplete_exit_context:{exit_reason}",
                        }
                    )
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
            monitor_result_written = True
            summary["monitors"] += 1

            if should_exit:
                pos.exit_trigger = exit_decision.trigger or exit_reason
                pos.exit_reason = exit_reason
                pos.exit_divergence_score = edge_ctx.divergence_score
                pos.exit_market_velocity_1h = edge_ctx.market_velocity_1h
                pos.exit_forward_edge = edge_ctx.forward_edge
                # T4.2-Phase1 2026-04-23 (D4 audit-only symmetry check):
                # for the three statistically-asymmetric exit triggers
                # (EDGE_REVERSAL, BUY_NO_EDGE_EXIT, BUY_NO_NEAR_EXIT —
                # each uses 2 consecutive cycles with no FDR vs entry's
                # bootstrap CI + BH-FDR), load the entry evidence
                # envelope (written by T4.1b on ENTRY_ORDER_POSTED),
                # construct the current exit evidence reflecting the
                # weak 2-cycle burden, and call
                # DecisionEvidence.assert_symmetric_with. On
                # EvidenceAsymmetryError, emit a structured JSON warning
                # log so Phase1 can measure audit_log_false_positive_rate
                # over the 7-day gate to T4.2-Phase2. NEVER blocks the
                # exit. Force-majeure triggers (SETTLEMENT_IMMINENT /
                # WHALE_TOXICITY / MODEL_DIVERGENCE_PANIC /
                # FLASH_CRASH_PANIC / RED_FORCE_EXIT / VIG_EXTREME /
                # DAY0_OBSERVATION_REVERSAL) skip — symmetry does not
                # apply to non-statistical exits.
                if pos.exit_trigger in _D4_ASYMMETRIC_EXIT_TRIGGERS and conn is not None:
                    try:
                        from src.state.decision_chain import load_entry_evidence
                        entry_evidence = load_entry_evidence(conn, pos.trade_id)
                        if entry_evidence is not None:
                            exit_evidence = DecisionEvidence(
                                evidence_type="exit",
                                statistical_method="consecutive_confirmation",
                                sample_size=2,
                                # no FDR on exit — confidence_level=1.0 is
                                # a contract-satisfying placeholder per
                                # __post_init__ (0, 1] bound; the semantic
                                # "no alpha" is expressed by
                                # fdr_corrected=False below.
                                confidence_level=1.0,
                                fdr_corrected=False,
                                consecutive_confirmations=2,
                            )
                            try:
                                exit_evidence.assert_symmetric_with(entry_evidence)
                            except EvidenceAsymmetryError as asym:
                                deps.logger.warning(
                                    "exit_evidence_asymmetry "
                                    + json.dumps(
                                        {
                                            "trigger": pos.exit_trigger,
                                            "trade_id": pos.trade_id,
                                            "entry_evidence_envelope": entry_evidence.to_json(),
                                            "exit_evidence_envelope": exit_evidence.to_json(),
                                            "error": str(asym),
                                            "timestamp": deps._utcnow().isoformat(),
                                        },
                                        sort_keys=True,
                                    )
                                )
                                summary["exit_evidence_asymmetry_audit"] = (
                                    summary.get("exit_evidence_asymmetry_audit", 0) + 1
                                )
                    except Exception as audit_exc:
                        # Audit MUST NOT block the exit. Emit the same
                        # `<key> + json.dumps({...}, sort_keys=True)` shape
                        # as the asymmetry path so Phase2's FP-rate
                        # aggregator has one parse path, not two.
                        deps.logger.warning(
                            "exit_evidence_audit_skipped "
                            + json.dumps(
                                {
                                    "trade_id": pos.trade_id,
                                    "reason": str(audit_exc),
                                    "timestamp": deps._utcnow().isoformat(),
                                },
                                sort_keys=True,
                            )
                        )
                        summary["exit_evidence_audit_skipped"] = (
                            summary.get("exit_evidence_audit_skipped", 0) + 1
                        )
                exit_intent = build_exit_intent(
                    pos,
                    replace(exit_context, exit_reason=exit_reason),
                )
                outcome = execute_exit(
                    portfolio=portfolio,
                    position=pos,
                    exit_context=replace(exit_context, exit_reason=exit_reason),
                    clob=clob,
                    conn=conn,
                    exit_intent=exit_intent,
                )
                if "paper_exit" in outcome or "exit_filled" in outcome:
                    tracker.record_exit(pos)
                    tracker_dirty = True
                summary["exits"] += 1
                portfolio_dirty = True
        except Exception as e:
            deps.logger.error("Monitor failed for %s: %s", pos.trade_id, e)
            summary["monitor_failed"] = summary.get("monitor_failed", 0) + 1
            reason_prefix = "time_context_failed" if hours_to_settlement is None else f"refresh_failed:{e.__class__.__name__}"
            if hours_to_settlement is None:
                try:
                    city = deps.cities_by_name.get(pos.city)
                    if city is not None:
                        lead_hours_to_settlement_close(pos.target_date, city.timezone, deps._utcnow())
                except Exception:
                    reason_prefix = f"time_context_failed:{e.__class__.__name__}"
            near_settlement = (
                hours_to_settlement is None
                or hours_to_settlement <= 6.0
                or pos.state in {"day0_window", "pending_exit"}
            )
            if near_settlement and not monitor_result_written and "execution failed" not in str(e).lower():
                summary["monitor_chain_missing"] = summary.get("monitor_chain_missing", 0) + 1
                summary.setdefault("monitor_chain_missing_positions", []).append(pos.trade_id)
                summary.setdefault("monitor_chain_missing_reasons", []).append(
                    {"position_id": pos.trade_id, "reason": reason_prefix}
                )
                artifact.add_monitor_result(
                    deps.MonitorResult(
                        position_id=pos.trade_id,
                        fresh_prob=pos.last_monitor_prob or pos.p_posterior,
                        fresh_edge=pos.last_monitor_edge,
                        should_exit=False,
                        exit_reason=f"MONITOR_CHAIN_MISSING:{reason_prefix}",
                        neg_edge_count=pos.neg_edge_count,
                    )
                )

    return portfolio_dirty, tracker_dirty


def fetch_day0_observation(city, target_date: str, decision_time, *, deps):
    getter = deps.get_current_observation
    try:
        return getter(city, target_date=target_date, reference_time=decision_time)
    except TypeError:
        return getter(city)


def _availability_status_for_exception(exc: Exception) -> str:
    name = exc.__class__.__name__
    text = str(exc).lower()
    if "429" in text or "rate" in text or "limit" in text:
        return "RATE_LIMITED"
    if name == "MissingCalibrationError":
        return "DATA_STALE"
    if name == "ObservationUnavailableError":
        return "DATA_UNAVAILABLE"
    if "chain" in text:
        return "CHAIN_UNAVAILABLE"
    return "DATA_UNAVAILABLE"


def execute_discovery_phase(conn, clob, portfolio, artifact, tracker, limits, mode, summary: dict, entry_bankroll: float, decision_time, *, env: str, deps):
    portfolio_dirty = False
    tracker_dirty = False
    market_candidate_ctor = getattr(deps, "MarketCandidate", None)
    if market_candidate_ctor is None:
        from src.engine.evaluator import MarketCandidate as market_candidate_ctor
    # Slice P3-fix1b (post-review side-fix, 2026-04-26): _normalize_
    # temperature_metric must be imported unconditionally — pre-fix
    # the import sat inside `if market_candidate_ctor is None:` so the
    # external-deps path (deps.MarketCandidate provided) left
    # _normalize_temperature_metric undefined when L1133 referenced it,
    # raising UnboundLocalError that the entry path caught and
    # auto-paused entries. P2-fix3 latent bug surfaced post-merge.
    from src.engine.evaluator import _normalize_temperature_metric

    def _record_opportunity_fact(candidate, decision, *, should_trade: bool, rejection_stage: str, rejection_reasons: list[str]):
        try:
            from src.state.db import log_opportunity_fact

            log_opportunity_fact(
                conn,
                candidate=candidate,
                decision=decision,
                should_trade=should_trade,
                rejection_stage=rejection_stage,
                rejection_reasons=rejection_reasons,
                recorded_at=decision_time.isoformat(),
            )
        except Exception as exc:
            deps.logger.warning(
                "Opportunity fact write failed for %s: %s",
                getattr(decision, "decision_id", ""),
                exc,
            )

    def _record_probability_trace(candidate, decision):
        try:
            from src.state.db import log_probability_trace_fact

            result = log_probability_trace_fact(
                conn,
                candidate=candidate,
                decision=decision,
                recorded_at=decision_time.isoformat(),
                mode=mode.value,
            )
            if result.get("status") != "written":
                deps.logger.warning(
                    "Probability trace not written for %s: %s",
                    getattr(decision, "decision_id", ""),
                    result.get("status"),
                )
        except Exception as exc:
            deps.logger.warning(
                "Probability trace write failed for %s: %s",
                getattr(decision, "decision_id", ""),
                exc,
            )

    def _availability_scope_key(*, candidate=None, city_name: str = "", target_date: str = "") -> str:
        if candidate is not None:
            event_id = str(getattr(candidate, "event_id", "") or "").strip()
            if event_id:
                return event_id
            slug = str(getattr(candidate, "slug", "") or "").strip()
            if slug:
                return slug
            city_name = city_name or str(getattr(getattr(candidate, "city", None), "name", "") or "")
            target_date = target_date or str(getattr(candidate, "target_date", "") or "")
        if city_name and target_date:
            return f"{city_name}:{target_date}"
        return city_name or target_date or "unknown"

    def _availability_failure_type(status: str, reasons: list[str]) -> str:
        normalized = str(status or "").strip().upper()
        reason_text = " ".join(reasons).lower()
        if normalized == "RATE_LIMITED":
            return "rate_limited"
        if normalized == "CHAIN_UNAVAILABLE":
            return "chain_unavailable"
        if normalized == "DATA_STALE":
            return "data_stale"
        if "observation" in reason_text or "obs " in reason_text or reason_text.startswith("obs"):
            return "observation_missing"
        if "ens" in reason_text:
            return "ens_missing"
        return "data_unavailable"

    def _record_availability_fact(
        *,
        status: str,
        reasons: list[str],
        scope_type: str,
        scope_key: str,
        details: dict,
    ):
        normalized = str(status or "").strip().upper()
        if not normalized or normalized == "OK":
            return
        try:
            from src.state.db import log_availability_fact

            failure_type = _availability_failure_type(normalized, reasons)
            availability_id = ":".join(
                part
                for part in (
                    "availability",
                    scope_type,
                    scope_key,
                    decision_time.isoformat(),
                    failure_type,
                )
                if part
            )
            log_availability_fact(
                conn,
                availability_id=availability_id,
                scope_type=scope_type,
                scope_key=scope_key,
                failure_type=failure_type,
                started_at=decision_time.isoformat(),
                ended_at=decision_time.isoformat(),
                impact="skip",
                details=details,
            )
        except Exception as exc:
            deps.logger.warning("Availability fact write failed for %s: %s", scope_key, exc)

    params = deps.MODE_PARAMS[mode]
    markets = deps.find_weather_markets(min_hours_to_resolution=params.get("min_hours_to_resolution", 6))
    if "max_hours_since_open" in params:
        markets = [m for m in markets if m["hours_since_open"] < params["max_hours_since_open"]]
    if "min_hours_since_open" in params:
        markets = [m for m in markets if m["hours_since_open"] >= params["min_hours_since_open"]]
    if "max_hours_to_resolution" in params:
        markets = [m for m in markets if m.get("hours_to_resolution") is not None and m["hours_to_resolution"] < params["max_hours_to_resolution"]]

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
                availability_status = _availability_status_for_exception(e)
                _record_availability_fact(
                    status=availability_status,
                    reasons=[str(e)],
                    scope_type="city_target",
                    scope_key=_availability_scope_key(city_name=city.name, target_date=market["target_date"]),
                    details={
                        "city": city.name,
                        "target_date": market["target_date"],
                        "mode": mode.value,
                        "availability_status": availability_status,
                        "failure_reason": str(e),
                        "event_id": market.get("event_id", ""),
                        "slug": market.get("slug", ""),
                    },
                )
                artifact.add_no_trade(
                    deps.NoTradeCase(
                        decision_id="",
                        city=city.name,
                        target_date=market["target_date"],
                        range_label="",
                        direction="unknown",
                        rejection_stage="SIGNAL_QUALITY",
                        strategy_key="",
                        strategy="",
                        edge_source="",
                        availability_status=availability_status,
                        rejection_reasons=[str(e)],
                        market_hours_open=market.get("hours_since_open"),
                        timestamp=decision_time.isoformat(),
                    )
                )
                summary["no_trades"] += 1
                continue
            raise

        candidate = market_candidate_ctor(
            city=city,
            target_date=market["target_date"],
            outcomes=market["outcomes"],
            hours_since_open=market["hours_since_open"],
            hours_to_resolution=market["hours_to_resolution"],
            # Slice P2-fix3 (post-review C1 from critic, 2026-04-26): route
            # through canonical normalizer (post-A3 raises on missing/invalid)
            # instead of double-defensive `... or "high"` silent default.
            # If market dict lacks temperature_metric, the scanner upstream
            # has a bug worth surfacing — fail loud rather than silently
            # stamping HIGH onto every LOW market.
            temperature_metric=_normalize_temperature_metric(
                market.get("temperature_metric")
            ).temperature_metric,
            event_id=market.get("event_id", ""),
            slug=market.get("slug", ""),
            observation=obs,
            discovery_mode=mode.value,
        )
        summary["candidates"] += 1

        try:
            # B091: forward the cycle's authoritative decision_time to the
            # evaluator so per-cycle `recorded_at` timestamps derive from
            # the cycle boundary rather than being silently re-fabricated
            # as `datetime.now()` inside the evaluator per-candidate.
            decisions = deps.evaluate_candidate(
                candidate, conn, portfolio, clob, limits,
                entry_bankroll=entry_bankroll,
                decision_time=decision_time,
            )
            if decisions:
                # Accumulate FDR health metrics into cycle summary
                if any(getattr(d, "fdr_fallback_fired", False) for d in decisions):
                    summary["fdr_fallback_fired"] = True
                family_sizes = [getattr(d, "fdr_family_size", 0) for d in decisions if getattr(d, "fdr_family_size", 0) > 0]
                if family_sizes:
                    summary["fdr_family_size"] = summary.get("fdr_family_size", 0) + family_sizes[0]
                for trace_decision in decisions:
                    _record_probability_trace(candidate, trace_decision)
                try:
                    from src.engine.time_context import lead_hours_to_date_start, lead_hours_to_settlement_close
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
                        lead_hours=float(lead_hours_to_date_start(date.fromisoformat(candidate.target_date), city.timezone, decision_time)),
                    )
                except Exception as exc:
                    deps.logger.error("telemetry write failed, cycle flagged degraded: %s", exc)
                    summary["degraded"] = True
            for d in decisions:
                if False:
                    _ = d.calibration
                strategy_key = _resolve_strategy_key(d) if d.edge else ""
                if d.should_trade and d.edge and d.tokens:
                    if not strategy_key:
                        summary["no_trades"] += 1
                        rejection_stage = "SIGNAL_QUALITY"
                        rejection_reasons = ["invalid_or_missing_strategy_key"]
                        _record_opportunity_fact(
                            candidate,
                            d,
                            should_trade=False,
                            rejection_stage=rejection_stage,
                            rejection_reasons=rejection_reasons,
                        )
                        artifact.add_no_trade(
                            deps.NoTradeCase(
                                decision_id=d.decision_id,
                                city=city.name,
                                target_date=candidate.target_date,
                                range_label=d.edge.bin.label if d.edge else "",
                                direction=d.edge.direction if d.edge else "",
                                rejection_stage=rejection_stage,
                                strategy="",
                                strategy_key="",
                                edge_source=d.edge_source or deps._classify_edge_source(mode, d.edge),
                                availability_status=getattr(d, "availability_status", ""),
                                rejection_reasons=rejection_reasons,
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
                                market_hours_open=candidate.hours_since_open,
                                agreement=getattr(d, "agreement", ""),
                                timestamp=decision_time.isoformat(),
                            )
                        )
                        continue
                    strategy_name = strategy_key
                    if not deps.is_strategy_enabled(strategy_name):
                        edge_source = d.edge_source or deps._classify_edge_source(mode, d.edge)
                        summary["no_trades"] += 1
                        summary["strategy_gate_rejections"] = summary.get("strategy_gate_rejections", 0) + 1
                        rejection_stage = "RISK_REJECTED"
                        rejection_reasons = [f"strategy_gate_disabled:{strategy_name}"]
                        _record_opportunity_fact(
                            candidate,
                            d,
                            should_trade=False,
                            rejection_stage=rejection_stage,
                            rejection_reasons=rejection_reasons,
                        )
                        artifact.add_no_trade(
                            deps.NoTradeCase(
                                decision_id=d.decision_id,
                                city=city.name,
                                target_date=candidate.target_date,
                                range_label=d.edge.bin.label if d.edge else "",
                                direction=d.edge.direction if d.edge else "",
                                rejection_stage=rejection_stage,
                                strategy=strategy_name,
                                strategy_key=strategy_name,
                                edge_source=edge_source,
                                availability_status=getattr(d, "availability_status", ""),
                                rejection_reasons=rejection_reasons,
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
                                market_hours_open=candidate.hours_since_open,
                                agreement=getattr(d, "agreement", ""),
                                timestamp=decision_time.isoformat(),
                            )
                        )
                        continue
                    _record_opportunity_fact(
                        candidate,
                        d,
                        should_trade=True,
                        rejection_stage="",
                        rejection_reasons=[],
                    )
                    intent = deps.create_execution_intent(
                        edge_context=d.edge_context,
                        edge=d.edge,
                        size_usd=d.size_usd,
                        mode=mode.value,
                        market_id=d.tokens["market_id"],
                        token_id=d.tokens["token_id"],
                        no_token_id=d.tokens["no_token_id"],
                        event_id=(
                            candidate.event_id
                            or candidate.slug
                            or f"{city.name}:{candidate.target_date}"
                        ),
                        resolution_window=candidate.target_date,
                        correlation_key=(
                            f"{getattr(city, 'cluster', '') or city.name}:{candidate.target_date}"
                        ),
                    )
                    # P1.S5: thread decision_id from d.decision_id so the
                    # executor can use a stable upstream ID for idempotency.
                    # We do NOT pass conn from cycle_runtime (P2 concern: the
                    # cycle conn targets zeus.db, not zeus_trades.db where
                    # venue_commands live). The executor opens its own
                    # get_trade_connection_with_world() fallback.
                    result = deps.execute_intent(
                        intent,
                        d.edge.vwmp,
                        d.edge.bin.label,
                        decision_id=str(d.decision_id) if d.decision_id else "",
                    )
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
                            "strategy_key": strategy_name,
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
                    # P1.S5 INV-32: materialize_position advances position
                    # authority ONLY after the venue command reached a durable
                    # ack state (ACKED, PARTIAL, FILLED). Commands in
                    # SUBMITTING / UNKNOWN do not yield position rows;
                    # the recovery loop resolves them out-of-band.
                    _cmd_state = result.command_state  # str | None
                    _cmd_durable = _cmd_state in ("ACKED", "PARTIAL", "FILLED")
                    _cmd_in_flight = _cmd_state in ("SUBMITTING", "UNKNOWN")
                    if result.status in ("filled", "pending") and _cmd_durable:
                        pos = materialize_position(
                            candidate,
                            d,
                            result,
                            portfolio,
                            city,
                            mode,
                            state=initial_entry_runtime_state_for_order_status(result.status),
                            env=env,
                            bankroll_at_entry=entry_bankroll,
                            deps=deps,
                        )
                        deps.add_position(portfolio, pos)
                        from src.state.db import log_execution_report, log_trade_entry

                        sp_name = f"sp_candidate_{str(d.decision_id).replace('-', '_')}"
                        conn.execute(f"SAVEPOINT {sp_name}")
                        try:
                            log_trade_entry(conn, pos)
                            log_execution_report(conn, pos, result, decision_id=d.decision_id)
                            # Post-audit fix #2 (2026-04-24): dual-write moved
                            # INSIDE sp_candidate_* — DR-33-B (commit 2a62623)
                            # replaced with-conn inside append_many_and_project
                            # with explicit nested SAVEPOINT, so placing the
                            # dual-write here no longer releases sp_candidate_*
                            # on commit. Closes torn-state window per T4.0 F3.
                            _dual_write_canonical_entry_if_available(
                                conn,
                                pos,
                                decision_id=d.decision_id,
                                deps=deps,
                                decision_evidence=getattr(d, "decision_evidence", None),
                            )
                            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                        except Exception:
                            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                            raise
                        portfolio_dirty = True
                        if result.status == "filled":
                            tracker.record_entry(pos)
                            tracker_dirty = True
                            summary["trades"] += 1
                    elif result.status in ("filled", "pending") and not _cmd_durable:
                        # INV-32: command in SUBMITTING/UNKNOWN or command_state=None
                        # (pre-P1.S5 path or executor rejected before persist).
                        # Do not materialize; recovery loop will resolve.
                        if _cmd_in_flight:
                            logger.warning(
                                "INV-32: skipping materialize_position for trade_id=%s "
                                "command_state=%s (in-flight; recovery will resolve)",
                                result.trade_id,
                                _cmd_state,
                            )
                        else:
                            logger.warning(
                                "INV-32: skipping materialize_position for trade_id=%s "
                                "command_state=%s (no durable ack)",
                                result.trade_id,
                                _cmd_state,
                            )
                    else:
                        from src.state.db import log_execution_report

                        log_execution_report(
                            conn,
                            _execution_stub(candidate, d, result, city, mode, deps=deps),
                            result,
                            decision_id=d.decision_id,
                        )
                else:
                    edge_source = ""
                    strategy_name = strategy_key
                    rejection_stage = d.rejection_stage
                    rejection_reasons = list(d.rejection_reasons)
                    if d.edge:
                        edge_source = d.edge_source or deps._classify_edge_source(mode, d.edge)
                        if not strategy_name:
                            rejection_stage = "SIGNAL_QUALITY"
                            rejection_reasons = [*rejection_reasons, "invalid_or_missing_strategy_key"]
                    availability_status = str(getattr(d, "availability_status", "") or "")
                    if availability_status:
                        _record_availability_fact(
                            status=availability_status,
                            reasons=rejection_reasons,
                            scope_type="candidate" if d.decision_id else "city_target",
                            scope_key=(
                                d.decision_id
                                if d.decision_id
                                else _availability_scope_key(candidate=candidate)
                            ),
                            details={
                                "decision_id": d.decision_id,
                                "candidate_id": _availability_scope_key(candidate=candidate),
                                "city": city.name,
                                "target_date": candidate.target_date,
                                "range_label": d.edge.bin.label if d.edge else "",
                                "direction": d.edge.direction if d.edge else "unknown",
                                "rejection_stage": rejection_stage,
                                "rejection_reasons": rejection_reasons,
                                "availability_status": availability_status,
                                "strategy_key": strategy_name,
                            },
                        )
                    _record_opportunity_fact(
                        candidate,
                        d,
                        should_trade=False,
                        rejection_stage=rejection_stage,
                        rejection_reasons=rejection_reasons,
                    )
                    summary["no_trades"] += 1
                    artifact.add_no_trade(
                        deps.NoTradeCase(
                            decision_id=d.decision_id,
                            city=city.name,
                            target_date=candidate.target_date,
                            range_label=d.edge.bin.label if d.edge else "",
                            direction=d.edge.direction if d.edge else "",
                            rejection_stage=rejection_stage,
                            strategy=strategy_name,
                            strategy_key=strategy_name,
                            edge_source=edge_source,
                            availability_status=getattr(d, "availability_status", ""),
                            rejection_reasons=rejection_reasons,
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
                            market_hours_open=candidate.hours_since_open,
                            agreement=getattr(d, "agreement", ""),
                            timestamp=decision_time.isoformat(),
                        )
                    )
        except Exception as e:
            deps.logger.error("Evaluation failed for %s %s: %s", city.name, candidate.target_date, e)

    return portfolio_dirty, tracker_dirty
