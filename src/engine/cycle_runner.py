"""CycleRunner orchestration surface.

Discovery modes share one runner. Heavy lifecycle/housekeeping logic lives in
`cycle_runtime.py`; this module keeps the orchestrator and its monkeypatch
surface stable.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from src.config import STATE_DIR, cities_by_name, get_mode, settings
from src.control import cutover_guard
from src.control.control_plane import has_acknowledged_quarantine_clear, is_entries_paused, is_strategy_enabled
# 2026-05-04 (live-block antibody — structural fix #4): single source of truth
# for "why are entries blocked right now?" across all 13 stacked gates.
# Phase 1 is observational — registry snapshot is logged + emitted into the
# cycle JSON before the existing L752 short-circuit.  Existing logic unchanged.
# See docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
from src.control.entries_block_registry import (
    BlockStage,
    BlockState,
    EntriesBlockRegistry,
)
from src.control.block_adapters._base import RegistryDeps
# S-4 fix (architect audit 2026-04-30, recovery 2026-05-01): module-level import
# so test monkeypatch.setattr(cr_module, "evaluate_freshness_mid_run", ...) takes effect.
# Per-cycle freshness consumer wired into run_cycle() top to short-circuit DAY0_CAPTURE
# and tag OPENING_HUNT degraded_data when source_health.json shows stale upstreams.
from src.control.freshness_gate import evaluate_freshness_mid_run
from src.data.market_scanner import (
    capture_executable_market_snapshot,
    find_weather_markets,
    get_last_scan_authority,
)
from src.data.observation_client import get_current_observation
from src.data.polymarket_client import PolymarketClient
from src.engine import cycle_runtime as _runtime
from src.engine.discovery_mode import DiscoveryMode
from src.engine.evaluator import EdgeDecision, MarketCandidate, evaluate_candidate
from src.execution.command_bus import IdempotencyKey, IntentKind
from src.execution.executor import (
    _persist_pre_submit_envelope,
    create_execution_intent,
    execute_intent,
)
from src.riskguard.risk_level import RiskLevel
from src.riskguard.riskguard import get_current_level, get_force_exit_review, tick_with_portfolio
from src.state.canonical_write import commit_then_export
from src.state.chain_reconciliation import ChainPosition, reconcile as reconcile_with_chain
from src.state.db import get_trade_connection_with_world, record_token_suppression
from src.state.lifecycle_manager import TERMINAL_STATES, is_terminal_state

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
from src.strategy.oracle_penalty import reload as oracle_penalty_reload
from src.strategy.risk_limits import RiskLimits

logger = logging.getLogger(__name__)

KNOWN_STRATEGIES = {"settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"}

# DT#2 P9B (INV-19): terminal position states are excluded from the RED
# force-exit sweep. Slice B1 (PR #19 finding 9, 2026-04-26) collapsed the
# prior local frozenset into the canonical TERMINAL_STATES owned by
# src.state.lifecycle_manager (derived programmatically from
# LEGAL_LIFECYCLE_FOLDS so future fold edits cannot drift from this site).
_TERMINAL_POSITION_STATES_FOR_SWEEP = TERMINAL_STATES


def _execute_force_exit_sweep(
    portfolio: PortfolioState,
    *,
    conn=None,
    now: datetime | None = None,
) -> dict:
    """DT#2 / INV-19 RED force-exit sweep (Phase 9B).

    Marks all active (non-terminal) positions with `exit_reason="red_force_exit"`
    so the existing exit_lifecycle machinery picks them up on the next
    monitor_refresh cycle and posts sell orders through the normal exit lane.

    Does NOT post sell orders in-cycle — keeps the sweep low-risk + testable.
    Already-exiting positions (non-empty `exit_reason` from a prior exit flow)
    are NOT overridden — we mark only positions that have no exit flow yet.

    Law reference: docs/authority/zeus_current_architecture.md §17 +
    docs/authority/zeus_dual_track_architecture.md §6 DT#2. Pre-P9B behavior
    was entry-block-only (Phase 1 scope); this closes the Phase 2 sweep gap.

    When ``conn`` is supplied, M1 additionally emits durable CANCEL proxy
    commands for swept positions that carry enough executable-market context.
    This remains side-effect-free: it records intent and a CANCEL_REQUESTED
    journal event only; M4/M5 own actual cancel/replace and reconciliation
    runtime.

    Returns:
        dict with counts: {attempted, already_exiting, skipped_terminal,
        cancel_commands_inserted, cancel_commands_existing,
        cancel_commands_skipped}
    """
    attempted = 0
    already_exiting = 0
    skipped_terminal = 0
    cancel_commands_inserted = 0
    cancel_commands_existing = 0
    cancel_commands_skipped = 0
    cancel_command_errors = 0
    now_dt = now or _utcnow()
    now_iso = now_dt.isoformat()
    if conn is not None:
        from src.state.venue_command_repo import (
            append_event,
            find_command_by_idempotency_key,
            insert_command,
        )

    for pos in portfolio.positions:
        # pos.state may be a LifecycleState enum (str-subclass) or a bare string;
        # under Python 3.14 str(enum) returns fully-qualified "ClassName.MEMBER",
        # so extract .value when available.
        raw_state = getattr(pos, "state", "") or ""
        state_val = str(getattr(raw_state, "value", raw_state)).strip().lower()
        if state_val in _TERMINAL_POSITION_STATES_FOR_SWEEP:
            skipped_terminal += 1
            continue
        existing_reason = str(getattr(pos, "exit_reason", "") or "").strip()
        if existing_reason:
            already_exiting += 1
            continue
        pos.exit_reason = "red_force_exit"
        attempted += 1
        if conn is not None:
            try:
                venue_order_id = (
                    getattr(pos, "order_id", None)
                    or getattr(pos, "entry_order_id", None)
                    or getattr(pos, "last_exit_order_id", None)
                )
                snapshot_id = str(getattr(pos, "decision_snapshot_id", "") or "").strip()
                token_id = _held_token_id(pos)
                price = _red_proxy_price(pos)
                size = _red_proxy_size(pos)
                if not venue_order_id or not snapshot_id or not token_id or price is None or size is None:
                    outcome = "skipped"
                else:
                    decision_id = f"red_force_exit_proxy:{getattr(pos, 'trade_id', '') or token_id}"
                    side = "SELL"
                    idempotency_key = IdempotencyKey.from_inputs(
                        decision_id=decision_id,
                        token_id=token_id,
                        side=side,
                        price=price,
                        size=size,
                        intent_kind=IntentKind.CANCEL,
                    ).value
                    if find_command_by_idempotency_key(conn, idempotency_key) is not None:
                        outcome = "existing"
                    else:
                        command_id = f"red-cancel-{idempotency_key[:16]}"
                        envelope_id = _persist_pre_submit_envelope(
                            conn,
                            command_id=command_id,
                            snapshot_id=snapshot_id,
                            token_id=token_id,
                            side=side,
                            price=price,
                            size=size,
                            order_type="GTC",
                            post_only=False,
                            captured_at=now_iso,
                        )
                        insert_command(
                            conn,
                            command_id=command_id,
                            snapshot_id=snapshot_id,
                            envelope_id=envelope_id,
                            position_id=str(getattr(pos, "trade_id", "") or token_id),
                            decision_id=decision_id,
                            idempotency_key=idempotency_key,
                            intent_kind=IntentKind.CANCEL.value,
                            market_id=str(
                                getattr(pos, "market_id", "")
                                or getattr(pos, "condition_id", "")
                                or "unknown"
                            ),
                            token_id=token_id,
                            side=side,
                            size=size,
                            price=price,
                            created_at=now_iso,
                            snapshot_checked_at=now_iso,
                            venue_order_id=str(venue_order_id),
                            reason="red_force_exit_proxy",
                        )
                        append_event(
                            conn,
                            command_id=command_id,
                            event_type="CANCEL_REQUESTED",
                            occurred_at=now_iso,
                            payload={
                                "reason": "red_force_exit_proxy",
                                "venue_order_id": str(venue_order_id),
                                "source": "cycle_runner._execute_force_exit_sweep",
                            },
                        )
                        outcome = "inserted"
            except Exception as exc:  # fail closed for command truth, preserve sweep mark
                cancel_command_errors += 1
                logger.warning(
                    "M1 RED cancel proxy emission failed for trade_id=%s: %s",
                    getattr(pos, "trade_id", ""),
                    exc,
                )
            else:
                if outcome == "inserted":
                    cancel_commands_inserted += 1
                elif outcome == "existing":
                    cancel_commands_existing += 1
                else:
                    cancel_commands_skipped += 1

    return {
        "attempted": attempted,
        "already_exiting": already_exiting,
        "skipped_terminal": skipped_terminal,
        "cancel_commands_inserted": cancel_commands_inserted,
        "cancel_commands_existing": cancel_commands_existing,
        "cancel_commands_skipped": cancel_commands_skipped,
        "cancel_command_errors": cancel_command_errors,
    }


def _held_token_id(pos: Position) -> str:
    direction = str(getattr(pos, "direction", "") or "").lower()
    if "no" in direction:
        return str(getattr(pos, "no_token_id", "") or "").strip()
    return str(getattr(pos, "token_id", "") or "").strip()


def _red_proxy_price(pos: Position) -> float | None:
    for attr in ("last_monitor_best_bid", "entry_price"):
        value = getattr(pos, attr, None)
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if 0 < price < 1:
            return price
    return None


def _red_proxy_size(pos: Position) -> float | None:
    value = getattr(pos, "shares", None)
    try:
        size = float(value)
    except (TypeError, ValueError):
        return None
    return size if size > 0 else None


def _risk_allows_new_entries(risk_level: RiskLevel) -> bool:
    return risk_level == RiskLevel.GREEN


# P0.3 (INV-27): observability-only surfacing of positions in execution-unsafe
# states. Operator decision 2026-04-26: surface warnings, do NOT block entries.
# K4 (P1+) will replace these heuristics with command-truth integration.
_PENDING_STATE_PREFIX = "pending_"
_QUARANTINED_STATE_VALUES = frozenset({"quarantined", "quarantine_expired"})


def _collect_execution_truth_warnings(portfolio: PortfolioState) -> list[dict]:
    """Scan portfolio for positions in execution-unsafe states.

    Returns a list of warning dicts. Each warning carries enough identity
    (trade_id, state) for an operator to investigate; we do not block entries.

    Detection rules (P0 conservative — pre-K4):
    - Position in any quarantined state with empty `order_id`
      → "quarantine_without_order_authority"
    - Position in any pending_* state with empty `order_id`
      → "pending_state_missing_order_id"

    Once K4 lands a durable command journal, these heuristics are replaced
    with command-truth lookup (UNKNOWN command authority for that position).
    """
    warnings: list[dict] = []
    for pos in portfolio.positions:
        raw_state = getattr(pos, "state", "") or ""
        state_val = str(getattr(raw_state, "value", raw_state)).strip().lower()
        order_id = str(getattr(pos, "order_id", "") or "").strip()
        trade_id = getattr(pos, "trade_id", "") or ""
        if state_val in _QUARANTINED_STATE_VALUES and not order_id:
            warnings.append({
                "type": "quarantine_without_order_authority",
                "trade_id": trade_id,
                "state": state_val,
                "reason": "Position is quarantined without order_id; no venue command authority to verify state.",
            })
        elif state_val.startswith(_PENDING_STATE_PREFIX) and not order_id:
            warnings.append({
                "type": "pending_state_missing_order_id",
                "trade_id": trade_id,
                "state": state_val,
                "reason": "Position in pending state without order_id; execution truth is unknown.",
            })
    return warnings


def _classify_edge_source(mode: DiscoveryMode, edge) -> str:
    if mode == DiscoveryMode.DAY0_CAPTURE:
        return "settlement_capture"
    if mode == DiscoveryMode.OPENING_HUNT:
        return "opening_inertia"
    if edge.direction == "buy_no" and edge.bin.is_shoulder:
        return "shoulder_sell"
    if edge.direction == "buy_yes" and not edge.bin.is_shoulder:
        return "center_buy"
    return "unclassified"


def _classify_strategy(mode: DiscoveryMode, edge, edge_source: str = "") -> str:
    if edge_source in KNOWN_STRATEGIES:
        return edge_source
    return _classify_edge_source(mode, edge)


MODE_PARAMS = {
    DiscoveryMode.OPENING_HUNT: {"max_hours_since_open": 24, "min_hours_to_resolution": 24},
    DiscoveryMode.UPDATE_REACTION: {"min_hours_since_open": 24, "min_hours_to_resolution": 6},
    DiscoveryMode.DAY0_CAPTURE: {"max_hours_to_resolution": 6},
}
PENDING_FILL_STATUSES = {"CONFIRMED"}
PENDING_CANCEL_STATUSES = {"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _run_chain_sync(portfolio: PortfolioState, clob, conn):
    return _runtime.run_chain_sync(portfolio, clob, conn=conn, deps=sys.modules[__name__])


def _cleanup_orphan_open_orders(portfolio: PortfolioState, clob, conn=None) -> int:
    return _runtime.cleanup_orphan_open_orders(portfolio, clob, deps=sys.modules[__name__], conn=conn)


def _entry_bankroll_for_cycle(portfolio: PortfolioState, clob):
    return _runtime.entry_bankroll_for_cycle(portfolio, clob, deps=sys.modules[__name__])


def _materialize_position(candidate, decision, result, portfolio, city, mode, *, state: str, env: str, bankroll_at_entry: float | None = None):
    return _runtime.materialize_position(
        candidate,
        decision,
        result,
        portfolio,
        city,
        mode,
        state=state,
        env=env,
        bankroll_at_entry=bankroll_at_entry,
        deps=sys.modules[__name__],
    )


def _reconcile_pending_positions(portfolio: PortfolioState, clob, tracker) -> dict:
    return _runtime.reconcile_pending_positions(portfolio, clob, tracker, deps=sys.modules[__name__])


def _execute_monitoring_phase(conn, clob: PolymarketClient, portfolio, artifact: CycleArtifact, tracker, summary: dict):
    return _runtime.execute_monitoring_phase(conn, clob, portfolio, artifact, tracker, summary, deps=sys.modules[__name__])


def _execute_discovery_phase(conn, clob, portfolio, artifact: CycleArtifact, tracker, limits, mode, summary: dict, entry_bankroll: float, decision_time: datetime, *, env: str):
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
        env=env,
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

    # S-4 fix (architect audit 2026-04-30, recovery 2026-05-01) — per-cycle
    # freshness gate. evaluate_freshness_mid_run is imported at module level so
    # tests can monkeypatch it. Three branches per design §3.1:
    #   FRESH    → fall through (normal cycle)
    #   STALE w/ day0_capture_disabled + DiscoveryMode.DAY0_CAPTURE → short-circuit
    #   STALE w/ ensemble_disabled + DiscoveryMode.OPENING_HUNT     → tag degraded_data, continue
    # The DAY0 short-circuit returns the summary BEFORE any IO so the trading stack
    # never touches stale upstream data. OPENING_HUNT continues with the flag set
    # so downstream entry decisions can be tagged in decision_log.
    try:
        _freshness_verdict = evaluate_freshness_mid_run(STATE_DIR)
    except Exception as exc:
        # Mid-run gate is fail-soft per design — log and proceed.
        logger.warning("freshness_gate mid_run evaluation failed: %s", exc)
        _freshness_verdict = None
    if _freshness_verdict is not None:
        if _freshness_verdict.day0_capture_disabled and mode == DiscoveryMode.DAY0_CAPTURE:
            summary["skipped"] = True
            summary["skip_reason"] = "cycle_skipped_freshness_degraded"
            summary["stale_sources"] = list(_freshness_verdict.stale_sources)
            return summary
        if _freshness_verdict.ensemble_disabled and mode == DiscoveryMode.OPENING_HUNT:
            summary["degraded_data"] = True
            summary["stale_sources"] = list(_freshness_verdict.stale_sources)

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
        require_provenance("kelly_mult")
    except Exception as e:
        logger.warning("Provenance registry precheck failed: %s", e)

    risk_level = get_current_level()
    summary["risk_level"] = risk_level.value

    conn = get_connection()
    portfolio = load_portfolio()
    if getattr(portfolio, 'portfolio_loader_degraded', False):
        # DT#6 graceful degradation (Phase 8 R-BQ): do NOT raise RuntimeError.
        # Run the degraded-mode riskguard tick so risk_level reflects DATA_DEGRADED
        # (riskguard.tick_with_portfolio surfaces the degraded authority into
        # overall_level). Downstream entry gates honour risk_level != GREEN,
        # suppressing new-entry paths while monitor / exit / reconciliation
        # lanes continue read-only. See docs/authority/zeus_dual_track_architecture.md
        # §6 DT#6 law: "process must not raise RuntimeError; disable new-entry
        # paths; keep monitor/exit/reconciliation running read-only".
        logger.warning(
            "Portfolio loader degraded — running DT#6 graceful-degradation cycle "
            "(new-entry paths suppressed via risk_level; monitor/exit/reconciliation continue)"
        )
        summary["portfolio_degraded"] = True
        risk_level = tick_with_portfolio(portfolio)
        # Phase 9A MINOR-M4: intentional overwrite of summary["risk_level"] set
        # at L176 from get_current_level() — the degraded tick's level supersedes
        # the pre-lookup per DT#6 semantics. Canonical value for this cycle is
        # whatever tick_with_portfolio returned (typically RiskLevel.DATA_DEGRADED).
        summary["risk_level"] = risk_level.value
    try:
        from src.control.heartbeat_supervisor import summary as _heartbeat_summary
        from src.control.ws_gap_guard import summary as _ws_gap_summary
        from src.risk_allocator import refresh_global_allocator

        _governor_start_heartbeat = _heartbeat_summary()
        _governor_start_ws = _ws_gap_summary()
        _baseline = float(getattr(portfolio, "daily_baseline_total", 0.0) or 0.0)
        _current_bankroll = float(getattr(portfolio, "bankroll", 0.0) or 0.0)
        _drawdown_pct = max(((_baseline - _current_bankroll) / _baseline) * 100.0, 0.0) if _baseline > 0 else 0.0
        summary["portfolio_governor_cycle_start"] = refresh_global_allocator(
            conn,
            ledger={"current_drawdown_pct": _drawdown_pct, "risk_level": risk_level.value},
            heartbeat=_governor_start_heartbeat,
            ws_status=_governor_start_ws,
        )
    except Exception as _governor_start_exc:
        logger.error(
            "PortfolioGovernor cycle-start refresh failed: %s; blocking new entries fail-closed",
            _governor_start_exc,
            exc_info=True,
        )
        summary["portfolio_governor_cycle_start"] = {
            "configured": False,
            "error": str(_governor_start_exc),
            "entry": {"allow_submit": False, "reason": "portfolio_governor_unavailable"},
        }
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

    try:
        chain_stats, chain_ready = _run_chain_sync(portfolio, clob, conn)
    except Exception as exc:
        logger.error("Chain sync FAILED — entries will be blocked: %s", exc, exc_info=True)
        chain_stats, chain_ready = {"error": str(exc)}, False
    if chain_stats:
        summary["chain_sync"] = chain_stats
        if chain_stats.get("synced") or chain_stats.get("voided") or chain_stats.get("quarantined") or chain_stats.get("updated"):
            portfolio_dirty = True

    from src.state.chain_reconciliation import check_quarantine_timeouts

    q_expired = check_quarantine_timeouts(portfolio)
    if q_expired:
        summary["quarantine_expired"] = q_expired
        portfolio_dirty = True

    try:
        stale_cancelled = _cleanup_orphan_open_orders(portfolio, clob, conn=conn)
    except Exception as exc:
        logger.warning("Orphan open-order cleanup failed — continuing cycle: %s", exc)
        stale_cancelled = 0
    if stale_cancelled:
        summary["stale_orders_cancelled"] = stale_cancelled

    # INV-31: command-recovery loop. Reconciles unresolved venue_commands
    # against venue state. Errors don't fail the cycle.
    try:
        from src.execution.command_recovery import reconcile_unresolved_commands
        rec_summary = reconcile_unresolved_commands()
        summary["command_recovery"] = rec_summary
    except Exception as exc:
        logger.error("command_recovery raised; continuing cycle: %s", exc, exc_info=True)
        summary["command_recovery"] = {"error": str(exc)}

    entry_bankroll, cap_summary = _entry_bankroll_for_cycle(portfolio, clob)
    summary.update({k: v for k, v in cap_summary.items() if v is not None})

    # B5 + DT#2 P9B: When daily_loss RED, block new entries AND sweep active
    # positions toward exit (previously Phase 1 was entry-block-only; Phase 9B
    # closes the sweep gap per zeus_dual_track_architecture.md §6 DT#2 law:
    # "RED must cancel all pending orders AND initiate an exit sweep on
    # active positions"). Sweep marks `exit_reason="red_force_exit"` on each
    # non-terminal, not-already-exiting position before monitor_refresh so the
    # existing exit_lifecycle/capability path can act in the same cycle instead
    # of waiting for the next daemon tick.
    force_exit = get_force_exit_review()
    red_risk_sweep = risk_level == RiskLevel.RED
    if force_exit or red_risk_sweep:
        if force_exit:
            summary["force_exit_review"] = True
        summary["force_exit_review_scope"] = "sweep_active_positions"
        summary["force_exit_sweep_trigger"] = (
            "force_exit_review" if force_exit else "risk_level_red"
        )
        sweep_result = _execute_force_exit_sweep(portfolio, conn=conn)
        summary["force_exit_sweep"] = sweep_result
        if sweep_result["attempted"] > 0:
            portfolio_dirty = True  # positions' exit_reason changed; persist
        logger.warning(
            "B5/DT#2: RED force-exit sweep active (trigger=%s). "
            "Sweep: attempted=%d already_exiting=%d skipped_terminal=%d.",
            summary["force_exit_sweep_trigger"],
            sweep_result["attempted"],
            sweep_result["already_exiting"],
            sweep_result["skipped_terminal"],
        )

    p_dirty, t_dirty = _execute_monitoring_phase(conn, clob, portfolio, artifact, tracker, summary)
    portfolio_dirty = portfolio_dirty or p_dirty
    tracker_dirty = tracker_dirty or t_dirty

    current_heat = portfolio_heat_for_bankroll(portfolio, entry_bankroll or 0.0)
    summary["portfolio_heat_pct"] = round(current_heat * 100.0, 2) if entry_bankroll else 0.0
    exposure_gate_hit = entry_bankroll is not None and entry_bankroll > 0 and current_heat >= limits.max_portfolio_heat_pct * 0.95

    # INV-27 / P0.3: surface execution-truth warnings for operator visibility.
    # Observability-only — never blocks entries (per operator decision 2026-04-26).
    # K4 (P1+) will replace this heuristic scan with command-journal truth.
    _exec_truth_warnings = _collect_execution_truth_warnings(portfolio)
    if _exec_truth_warnings:
        summary["execution_truth_warnings"] = _exec_truth_warnings

    entries_blocked_reason = None
    has_quarantine = any(
        pos.chain_state in {"quarantined", "quarantine_expired"}
        for pos in portfolio.positions
    )
    # 2026-05-04 bankroll truth-chain cleanup tail: the legacy ONE-TIME
    # `smoke_test_portfolio_cap_usd` aggregate-exposure brake (added 2026-04-12
    # after the first live cycle placed 12 orders intending $60 instead of one
    # $5 trade) has been removed. Smoke-testing must run as a separate one-off
    # script, not as a perma-gate that throttles real live trading. Per-cycle
    # exposure discipline now lives in the existing posture / RiskGuard /
    # max-exposure gates only.
    # INV-26 / O2-c posture gate: consult committed runtime_posture.yaml.
    # Posture is recorded in `summary["posture"]` for operator visibility on
    # every cycle. It also blocks new entries when non-NORMAL — but only as
    # the FALLBACK reason when no more-specific gate fires. Specific gates
    # (chain_sync, quarantine, force_exit, risk_level, bankroll, exposure,
    # entries_paused) take precedence so operators see actionable detail
    # rather than the outermost branch posture. Monitor, exit, and
    # reconciliation paths continue regardless of posture.
    _current_posture: str = "NO_NEW_ENTRIES"
    try:
        from src.runtime.posture import read_runtime_posture
        _current_posture = read_runtime_posture()
    except Exception as _posture_exc:
        logger.error(
            "runtime_posture read raised unexpectedly: %s; treating as NO_NEW_ENTRIES",
            _posture_exc,
            exc_info=True,
        )
        _current_posture = "NO_NEW_ENTRIES"
    summary["posture"] = _current_posture
    try:
        _cutover_summary = cutover_guard.summary()
    except Exception as _cutover_exc:
        logger.error(
            "CutoverGuard summary failed: %s; blocking new entries fail-closed",
            _cutover_exc,
            exc_info=True,
        )
        _cutover_summary = {
            "state": "BLOCKED",
            "error": str(_cutover_exc),
            "entry": {"allow_submit": False},
        }
    summary["cutover_guard"] = _cutover_summary
    try:
        from src.control.heartbeat_supervisor import summary as _heartbeat_summary
        _heartbeat_status = _heartbeat_summary()
    except Exception as _heartbeat_exc:
        logger.error(
            "HeartbeatSupervisor summary failed: %s; blocking new entries fail-closed",
            _heartbeat_exc,
            exc_info=True,
        )
        _heartbeat_status = {
            "health": "LOST",
            "error": str(_heartbeat_exc),
            "entry": {"allow_submit": False},
        }
    summary["heartbeat"] = _heartbeat_status
    try:
        from src.control.ws_gap_guard import summary as _ws_gap_summary
        _ws_gap_status = _ws_gap_summary()
    except Exception as _ws_gap_exc:
        logger.error(
            "WS user-channel guard summary failed: %s; blocking new entries fail-closed",
            _ws_gap_exc,
            exc_info=True,
        )
        _ws_gap_status = {
            "subscription_state": "DISCONNECTED",
            "gap_reason": str(_ws_gap_exc),
            "m5_reconcile_required": True,
            "entry": {"allow_submit": False},
        }
    summary["ws_user_channel"] = _ws_gap_status
    try:
        from src.risk_allocator import refresh_global_allocator

        _baseline = float(getattr(portfolio, "daily_baseline_total", 0.0) or 0.0)
        _current_bankroll = float(getattr(portfolio, "bankroll", 0.0) or 0.0)
        _drawdown_pct = max(((_baseline - _current_bankroll) / _baseline) * 100.0, 0.0) if _baseline > 0 else 0.0
        _governor_status = refresh_global_allocator(
            conn,
            ledger={"current_drawdown_pct": _drawdown_pct, "risk_level": risk_level.value},
            heartbeat=_heartbeat_status,
            ws_status=_ws_gap_status,
        )
    except Exception as _governor_exc:
        logger.error(
            "PortfolioGovernor summary failed: %s; blocking new entries fail-closed",
            _governor_exc,
            exc_info=True,
        )
        _governor_status = {
            "configured": False,
            "error": str(_governor_exc),
            "entry": {"allow_submit": False, "reason": "portfolio_governor_unavailable"},
        }
    summary["portfolio_governor"] = _governor_status
    if bool(_ws_gap_status.get("m5_reconcile_required", False)):
        summary["m5_reconcile_required"] = True
        summary["m5_reconcile_reason"] = f"ws_gap={_ws_gap_status.get('subscription_state', 'DISCONNECTED')}:{_ws_gap_status.get('gap_reason', '')}"

    if not chain_ready:
        entries_blocked_reason = "chain_sync_unavailable"
    elif has_quarantine:
        entries_blocked_reason = "portfolio_quarantined"
    elif force_exit:
        entries_blocked_reason = "force_exit_review_daily_loss_red"
    elif risk_level in (RiskLevel.YELLOW, RiskLevel.ORANGE, RiskLevel.RED, RiskLevel.DATA_DEGRADED):
        # Phase 9A R-BT: DATA_DEGRADED from DT#6 (portfolio_loader_degraded) must
        # populate entries_blocked_reason so operators see a reason code in
        # summary / status_summary / Discord reports. Pre-P9A: DATA_DEGRADED
        # fell through to None while entries were silently blocked.
        entries_blocked_reason = f"risk_level={risk_level.value}"
    elif entry_bankroll is None:
        entries_blocked_reason = cap_summary.get("entry_block_reason", "entry_bankroll_unavailable")
    elif entry_bankroll <= 0:
        entries_blocked_reason = "entry_bankroll_non_positive"
    elif exposure_gate_hit:
        entries_blocked_reason = "near_max_exposure"

    if has_quarantine:
        summary["portfolio_quarantined"] = True

    entries_paused = is_entries_paused()
    # entries_blocked_reason — observability only; not consulted by the short-circuit below (gate-purge 2026-05-04).
    if entries_paused and entries_blocked_reason is None:
        entries_blocked_reason = "entries_paused"
    if entries_blocked_reason is None and not bool((_cutover_summary.get("entry") or {}).get("allow_submit", False)):
        entries_blocked_reason = f"cutover_guard={_cutover_summary.get('state', 'BLOCKED')}"
    if entries_blocked_reason is None and not bool((_heartbeat_status.get("entry") or {}).get("allow_submit", False)):
        entries_blocked_reason = f"heartbeat={_heartbeat_status.get('health', 'LOST')}"
    if entries_blocked_reason is None and not bool((_ws_gap_status.get("entry") or {}).get("allow_submit", False)):
        entries_blocked_reason = f"ws_gap={_ws_gap_status.get('subscription_state', 'DISCONNECTED')}:{_ws_gap_status.get('gap_reason', '')}"
    if entries_blocked_reason is None and not bool((_governor_status.get("entry") or {}).get("allow_submit", True)):
        entries_blocked_reason = f"portfolio_governor={(_governor_status.get('entry') or {}).get('reason', 'blocked')}"
    # INV-26 final fallback: posture forbids new entries when no more-specific
    # gate fires. Recorded last so all actionable reasons take precedence;
    # posture surfaces only when it is the *sole* block.
    if entries_blocked_reason is None and _current_posture != "NORMAL":
        entries_blocked_reason = f"posture={_current_posture}"
    # ── REGISTRY-GUARDED SHORT-CIRCUIT (2026-05-04 antibody) ─────────────────
    # Phase 1: observational only.  Snapshot all 13 entries-block gates so
    # a single cycle JSON record answers "why are entries blocked right now?"
    # without grepping 5 modules.  CI gate
    # `tests/test_no_unregistered_block_predicate.py` enforces that any new
    # boolean appearing in the line below is also registered as an adapter.
    try:
        import os as _os
        from pathlib import Path as _Path
        from src.state.db import get_world_connection as _get_world_conn, get_connection as _get_db_conn, RISK_DB_PATH as _RISK_DB_PATH
        from src.riskguard import riskguard as _riskguard_mod
        from src.control import heartbeat_supervisor as _heartbeat_mod
        from src.control import ws_gap_guard as _ws_gap_mod
        from src.control import entry_forecast_rollout as _rollout_gate_mod
        _block_registry = EntriesBlockRegistry.from_runtime(
            RegistryDeps(
                state_dir=_Path(STATE_DIR),
                db_connection_factory=_get_world_conn,
                risk_state_db_connection_factory=lambda: _get_db_conn(_RISK_DB_PATH),
                riskguard_module=_riskguard_mod,
                heartbeat_module=_heartbeat_mod,
                ws_gap_guard_module=_ws_gap_mod,
                rollout_gate_module=_rollout_gate_mod,
                env=dict(_os.environ),
            )
        )
        _block_snapshot = _block_registry.enumerate_blocks(stage="all")
        _blocking_count = sum(1 for b in _block_snapshot if b.state == BlockState.BLOCKING)
        _unknown_count = sum(1 for b in _block_snapshot if b.state == BlockState.UNKNOWN)
        logger.info(
            "ENTRIES_BLOCK_REGISTRY_SNAPSHOT cycle=%s blocking=%d unknown=%d total=%d clear_discovery=%s",
            summary.get("cycle_id", "?"),
            _blocking_count,
            _unknown_count,
            len(_block_snapshot),
            _block_registry.is_clear(BlockStage.DISCOVERY),
        )
        summary["block_registry"] = [b.to_dict() for b in _block_snapshot]
    except Exception as _registry_exc:  # noqa: BLE001
        # Registry must never break the cycle — fail soft, log, continue.
        logger.warning(
            "ENTRIES_BLOCK_REGISTRY_SNAPSHOT_FAILED cycle=%s exc=%s: %s",
            summary.get("cycle_id", "?"),
            type(_registry_exc).__name__,
            _registry_exc,
            exc_info=True,
        )
        summary["block_registry_error"] = f"{type(_registry_exc).__name__}: {_registry_exc}"
    # Fail-CLOSED defaults (Ask 1 fix-up post critic-opus PR #54): if a
    # status dict is missing the "entry" key (contract not guaranteed by
    # heartbeat_supervisor.summary / ws_gap_guard.summary), default to
    # not allowing submit so we mirror the explicit fail-closed paths at
    # cycle_runner.py:752,754 above.
    if _risk_allows_new_entries(risk_level) and _heartbeat_status.get("entry", {}).get("allow_submit", False) and _ws_gap_status.get("entry", {}).get("allow_submit", False):
        try:
            p_dirty, t_dirty = _execute_discovery_phase(conn, clob, portfolio, artifact, tracker, limits, mode, summary, entry_bankroll, decision_time, env=get_mode())
            portfolio_dirty = portfolio_dirty or p_dirty
            tracker_dirty = tracker_dirty or t_dirty
        except Exception as exc:
            # Gate-purge 2026-05-04: auto-pause streak machinery retired.
            # Log the full traceback for diagnostics; daemon does NOT self-pause.
            logger.error("Entry path raised: %s", exc, exc_info=True)
    else:
        if entries_paused:
            summary["entries_paused"] = True
        if entries_blocked_reason is not None:
            summary["entries_blocked_reason"] = entries_blocked_reason
            if entries_blocked_reason == "near_max_exposure":
                summary["near_max_exposure"] = True

    artifact.completed_at = _utcnow().isoformat()

    # DT#1 / INV-17: DB commit FIRST, then JSON exports in order.
    # commit_then_export handles rollback-on-db-failure and
    # log-but-continue-on-json-failure.
    portfolio_should_save = portfolio_dirty or summary["trades"] > 0 or summary["exits"] > 0
    # Mutable container so closures can read the committed artifact_id.
    _artifact_id_box: list = [None]

    def _db_op() -> "int | None":
        aid = store_artifact(conn, artifact)
        _artifact_id_box[0] = aid
        return aid

    def _export_portfolio() -> None:
        if portfolio_should_save:
            save_portfolio(
                portfolio,
                last_committed_artifact_id=_artifact_id_box[0],
                source="cycle_housekeeping",  # Phase 9C B3 audit tag
            )

    def _export_tracker() -> None:
        if tracker_dirty:
            save_tracker(tracker)

    def _export_status() -> None:
        from src.observability.status_summary import write_status
        write_status(summary)

    try:
        commit_then_export(
            conn,
            db_op=_db_op,
            json_exports=[_export_portfolio, _export_tracker, _export_status],
        )
    except Exception as e:
        logger.warning("Decision chain recording failed: %s", e)

    conn.close()
    summary["completed_at"] = _utcnow().isoformat()

    logger.info(
        "Cycle %s: %d monitors, %d exits, %d candidates, %d trades",
        mode.value,
        summary["monitors"],
        summary["exits"],
        summary["candidates"],
        summary["trades"],
    )
    return summary
