"""CycleRunner orchestration surface.

Discovery modes share one runner. Heavy lifecycle/housekeeping logic lives in
`cycle_runtime.py`; this module keeps the orchestrator and its monkeypatch
surface stable.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from src.config import cities_by_name, get_mode, settings
from src.control.control_plane import has_acknowledged_quarantine_clear, is_entries_paused, is_strategy_enabled, pause_entries
from src.data.market_scanner import find_weather_markets
from src.data.observation_client import get_current_observation
from src.data.polymarket_client import PolymarketClient
from src.engine import cycle_runtime as _runtime
from src.engine.discovery_mode import DiscoveryMode
from src.engine.evaluator import EdgeDecision, MarketCandidate, evaluate_candidate
from src.execution.executor import create_execution_intent, execute_intent
from src.riskguard.risk_level import RiskLevel
from src.riskguard.riskguard import get_current_level, get_force_exit_review
from src.state.chain_reconciliation import ChainPosition, reconcile as reconcile_with_chain
from src.state.db import get_trade_connection_with_world, record_token_suppression

# Alias for dependency injection: fill_tracker.py and tests patch deps.get_connection.
# Default runtime seam must expose trade truth plus shared world truth.
get_connection = get_trade_connection_with_world
from src.state.decision_chain import CycleArtifact, MonitorResult, NoTradeCase, store_artifact
from src.state.portfolio import (
    Position,
    PortfolioState,
    add_position,
    close_position,
    load_portfolio,
    portfolio_heat_for_bankroll,
    save_portfolio,
    total_exposure_usd,
    void_position,
)
from src.state.strategy_tracker import get_tracker, save_tracker
from src.strategy.risk_limits import RiskLimits

logger = logging.getLogger(__name__)

KNOWN_STRATEGIES = {"settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"}


def _risk_allows_new_entries(risk_level: RiskLevel) -> bool:
    return risk_level == RiskLevel.GREEN


def _classify_edge_source(mode: DiscoveryMode, edge) -> str:
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
    if edge_source in KNOWN_STRATEGIES:
        return edge_source
    return _classify_edge_source(mode, edge)


MODE_PARAMS = {
    DiscoveryMode.OPENING_HUNT: {"max_hours_since_open": 24, "min_hours_to_resolution": 24},
    DiscoveryMode.UPDATE_REACTION: {"min_hours_since_open": 24, "min_hours_to_resolution": 6},
    DiscoveryMode.DAY0_CAPTURE: {"max_hours_to_resolution": 6},
}
PENDING_FILL_STATUSES = {"FILLED", "MATCHED"}
PENDING_CANCEL_STATUSES = {"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _run_chain_sync(portfolio: PortfolioState, clob, conn):
    return _runtime.run_chain_sync(portfolio, clob, conn=conn, deps=sys.modules[__name__])


def _cleanup_orphan_open_orders(portfolio: PortfolioState, clob) -> int:
    return _runtime.cleanup_orphan_open_orders(portfolio, clob, deps=sys.modules[__name__])


def _entry_bankroll_for_cycle(portfolio: PortfolioState, clob):
    return _runtime.entry_bankroll_for_cycle(portfolio, clob, deps=sys.modules[__name__])


def _materialize_position(candidate, decision, result, portfolio, city, mode, *, state: str, bankroll_at_entry: float | None = None):
    return _runtime.materialize_position(
        candidate,
        decision,
        result,
        portfolio,
        city,
        mode,
        state=state,
        bankroll_at_entry=bankroll_at_entry,
        deps=sys.modules[__name__],
    )


def _reconcile_pending_positions(portfolio: PortfolioState, clob, tracker) -> dict:
    return _runtime.reconcile_pending_positions(portfolio, clob, tracker, deps=sys.modules[__name__])


def _execute_monitoring_phase(conn, clob: PolymarketClient, portfolio, artifact: CycleArtifact, tracker, summary: dict):
    return _runtime.execute_monitoring_phase(conn, clob, portfolio, artifact, tracker, summary, deps=sys.modules[__name__])


def _execute_discovery_phase(conn, clob, portfolio, artifact: CycleArtifact, tracker, limits, mode, summary: dict, entry_bankroll: float, decision_time: datetime):
    return _runtime.execute_discovery_phase(
        conn,
        clob,
        portfolio,
        artifact,
        tracker,
        limits,
        mode,
        summary,
        entry_bankroll,
        decision_time,
        deps=sys.modules[__name__],
    )


def run_cycle(mode: DiscoveryMode) -> dict:
    decision_time = _utcnow()
    summary = {
        "mode": mode.value,
        "started_at": decision_time.isoformat(),
        "monitors": 0,
        "exits": 0,
        "candidates": 0,
        "trades": 0,
        "no_trades": 0,
    }
    artifact = CycleArtifact(mode=mode.value, started_at=summary["started_at"], summary=summary)

    try:
        from src.data.ensemble_client import _clear_cache as _clear_ensemble_cache
        _clear_ensemble_cache()
    except Exception as exc:
        logger.warning("ensemble cache clear failed: %s", exc)
    try:
        from src.data.market_scanner import _clear_active_events_cache
        _clear_active_events_cache()
    except Exception as exc:
        logger.warning("market scanner cache clear failed: %s", exc)
    try:
        from src.control.control_plane import process_commands
        process_commands()
    except Exception as e:
        logger.warning("Control plane precheck failed: %s", e)

    # C1/INV-13: one-time provenance registry validation — no-op mode
    try:
        from src.contracts.provenance_registry import require_provenance
        require_provenance("kelly_mult", requires_provenance=False)
    except Exception as e:
        logger.warning("Provenance registry precheck failed: %s", e)

    risk_level = get_current_level()
    summary["risk_level"] = risk_level.value

    conn = get_connection()
    portfolio = load_portfolio()
    clob = PolymarketClient()
    tracker = get_tracker()
    limits = RiskLimits()
    portfolio_dirty = False
    tracker_dirty = False

    pending_updates = _reconcile_pending_positions(portfolio, clob, tracker)
    portfolio_dirty = portfolio_dirty or pending_updates["dirty"]
    tracker_dirty = tracker_dirty or pending_updates["tracker_dirty"]
    summary["trades"] += pending_updates["entered"]
    summary["pending_voids"] = pending_updates["voided"]

    chain_stats, chain_ready = _run_chain_sync(portfolio, clob, conn)
    if chain_stats:
        summary["chain_sync"] = chain_stats
        if chain_stats.get("synced") or chain_stats.get("voided") or chain_stats.get("quarantined") or chain_stats.get("updated"):
            portfolio_dirty = True

    from src.state.chain_reconciliation import check_quarantine_timeouts

    q_expired = check_quarantine_timeouts(portfolio)
    if q_expired:
        summary["quarantine_expired"] = q_expired
        portfolio_dirty = True

    stale_cancelled = _cleanup_orphan_open_orders(portfolio, clob)
    if stale_cancelled:
        summary["stale_orders_cancelled"] = stale_cancelled

    entry_bankroll, cap_summary = _entry_bankroll_for_cycle(portfolio, clob)
    summary.update({k: v for k, v in cap_summary.items() if v is not None})

    p_dirty, t_dirty = _execute_monitoring_phase(conn, clob, portfolio, artifact, tracker, summary)
    portfolio_dirty = portfolio_dirty or p_dirty
    tracker_dirty = tracker_dirty or t_dirty

    # B5: When daily_loss RED, block new entries (Phase 1 scope: entry-blocking only;
    # forced exit sweep for active positions is a Phase 2 item)
    force_exit = get_force_exit_review()
    if force_exit:
        summary["force_exit_review"] = True
        summary["force_exit_review_scope"] = "entry_block_only"
        logger.warning("B5: force_exit_review active — daily loss RED. Scope: entry-block only.")

    current_heat = portfolio_heat_for_bankroll(portfolio, entry_bankroll or 0.0)
    summary["portfolio_heat_pct"] = round(current_heat * 100.0, 2) if entry_bankroll else 0.0
    exposure_gate_hit = entry_bankroll is not None and entry_bankroll > 0 and current_heat >= limits.max_portfolio_heat_pct * 0.95

    entries_blocked_reason = None
    has_quarantine = any(
        pos.chain_state in {"quarantined", "quarantine_expired"}
        for pos in portfolio.positions
    )
    # ONE-TIME smoke test portfolio cap — see settings.json note.
    # Blocks new entries once the sum of cost_basis_usd across all non-terminal
    # positions reaches the configured ceiling. Remove this branch together
    # with the setting after the first full lifecycle has been observed.
    try:
        smoke_test_cap = settings["smoke_test_portfolio_cap_usd"]
    except KeyError:
        smoke_test_cap = None
    open_cost_basis_usd = 0.0
    if smoke_test_cap is not None:
        terminal_phases = {"settled", "voided", "admin_closed"}
        open_cost_basis_usd = sum(
            float(getattr(pos, "cost_basis_usd", 0.0) or 0.0)
            for pos in portfolio.positions
            if str(getattr(pos, "phase", "") or "") not in terminal_phases
        )
        summary["smoke_test_open_cost_basis_usd"] = round(open_cost_basis_usd, 4)
        summary["smoke_test_portfolio_cap_usd"] = float(smoke_test_cap)
    if not chain_ready:
        entries_blocked_reason = "chain_sync_unavailable"
    elif has_quarantine:
        entries_blocked_reason = "portfolio_quarantined"
    elif force_exit:
        entries_blocked_reason = "force_exit_review_daily_loss_red"
    elif risk_level in (RiskLevel.ORANGE, RiskLevel.RED):
        entries_blocked_reason = f"risk_level={risk_level.value}"
    elif entry_bankroll is None:
        entries_blocked_reason = cap_summary.get("entry_block_reason", "entry_bankroll_unavailable")
    elif entry_bankroll <= 0:
        entries_blocked_reason = "entry_bankroll_non_positive"
    elif smoke_test_cap is not None and open_cost_basis_usd >= float(smoke_test_cap):
        entries_blocked_reason = f"smoke_test_portfolio_cap_reached({open_cost_basis_usd:.2f}>={float(smoke_test_cap):.2f})"
    elif exposure_gate_hit:
        entries_blocked_reason = "near_max_exposure"
    elif getattr(portfolio, 'portfolio_loader_degraded', False):
        entries_blocked_reason = "portfolio_loader_degraded"

    if has_quarantine:
        summary["portfolio_quarantined"] = True

    entries_paused = is_entries_paused()
    if _risk_allows_new_entries(risk_level) and not entries_paused and entries_blocked_reason is None:
        try:
            p_dirty, t_dirty = _execute_discovery_phase(conn, clob, portfolio, artifact, tracker, limits, mode, summary, entry_bankroll, decision_time)
            portfolio_dirty = portfolio_dirty or p_dirty
            tracker_dirty = tracker_dirty or t_dirty
        except Exception as exc:
            reason_code = f"auto_pause:{type(exc).__name__}"
            pause_entries(reason_code)
            logger.error("Entry path raised %s -- entries auto-paused: %s", type(exc).__name__, exc)
            summary["entries_paused"] = True
            summary["entries_pause_reason"] = reason_code
    else:
        if entries_paused:
            summary["entries_paused"] = True
        if entries_blocked_reason is not None:
            summary["entries_blocked_reason"] = entries_blocked_reason
            if entries_blocked_reason == "near_max_exposure":
                summary["near_max_exposure"] = True

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

    logger.info(
        "Cycle %s: %d monitors, %d exits, %d candidates, %d trades",
        mode.value,
        summary["monitors"],
        summary["exits"],
        summary["candidates"],
        summary["trades"],
    )
    return summary
