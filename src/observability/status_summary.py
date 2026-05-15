"""Status summary: written every cycle. Zeus is not a black box.

Blueprint v2 §10: 5-section health snapshot.
Written to a derived live status file for Venus/OpenClaw to read.
"""

import json
import logging
import os
from datetime import datetime, timezone

from src.config import get_mode, settings, state_path
from src.control.control_plane import (
    get_entries_pause_reason,
    get_entries_pause_source,
    get_edge_threshold_multiplier,
    is_entries_paused,
    recommended_autosafe_commands_from_status,
    recommended_commands_from_status,
    review_required_commands_from_status,
    strategy_gates,
)
from src.control.gate_decision import reason_refuted
from src.observability.calibration_serving_status import build_calibration_serving_status
from src.observability.price_evidence_report import (
    build_price_evidence_error_report,
    build_price_evidence_report,
)
from src.state.decision_chain import query_learning_surface_summary, query_lifecycle_funnel_report
from src.state.db import (
    get_trade_connection_with_world,
    query_execution_event_summary,
    query_position_current_status_view,
    query_strategy_health_snapshot,
)
from src.state.decision_chain import query_no_trade_cases
from src.state.truth_files import annotate_truth_payload, read_truth_json

logger = logging.getLogger(__name__)

STATUS_PATH = state_path("status_summary.json")
LEGACY_POSITIONS_PATH = state_path("positions.json")
_TERMINAL_LEGACY_POSITION_STATES = {
    "settled",
    "voided",
    "quarantined",
    "admin_closed",
    "closed",
    "exited",
}


def _enum_text(value, default: str) -> str:
    if value in (None, ""):
        return default
    return str(getattr(value, "value", value))


def _round_money_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _get_risk_level() -> str:
    """Read actual RiskGuard level instead of hardcoding GREEN."""
    try:
        from src.riskguard.riskguard import get_current_level
        return get_current_level().value
    except Exception:
        return "UNKNOWN"


def _get_risk_details() -> dict:
    try:
        import sqlite3

        conn = sqlite3.connect(str(state_path("risk_state.db")))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT details_json FROM risk_state ORDER BY checked_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row is None or not row["details_json"]:
            return {}
        details = json.loads(row["details_json"])
        return details if isinstance(details, dict) else {}
    except Exception:
        return {}


_V2_TABLES = (
    "platt_models_v2",
    "calibration_pairs_v2",
    "ensemble_snapshots_v2",
    "historical_forecasts_v2",
    "settlements_v2",
)

_V2_ROW_COUNT_SCHEMA_PREFERENCE = {
    "platt_models_v2": ("world", "main"),
    "calibration_pairs_v2": ("forecasts", "world", "main"),
    "ensemble_snapshots_v2": ("forecasts", "world", "main"),
    "historical_forecasts_v2": ("world", "main"),
    "settlements_v2": ("forecasts", "world", "main"),
}


def _quote_sql_identifier(identifier: str) -> str:
    text = str(identifier or "")
    if not text or text[0].isdigit() or not text.replace("_", "").isalnum():
        raise ValueError(f"unsafe sqlite identifier: {identifier!r}")
    return f'"{text}"'


def _attached_schema_names(conn) -> set[str]:
    try:
        return {
            str(row[1])
            for row in conn.execute("PRAGMA database_list").fetchall()
            if len(row) > 1 and row[1]
        }
    except Exception:
        return {"main"}


def _table_exists(conn, schema: str, table: str) -> bool:
    try:
        schema_sql = _quote_sql_identifier(schema)
        row = conn.execute(
            f"SELECT 1 FROM {schema_sql}.sqlite_master "
            "WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _bounded_table_row_count(conn, schema: str, table: str) -> int:
    """Return a cheap row-count signal without full-table COUNT(*) scans.

    These values are derived operator telemetry. For normal rowid tables,
    MAX(rowid) is an O(log n) high-water count signal and avoids scanning
    large world tables every cycle. If a future table has no rowid, fall back
    to a bounded non-empty sentinel rather than blocking status writes.
    """
    schema_sql = _quote_sql_identifier(schema)
    table_sql = _quote_sql_identifier(table)
    try:
        row = conn.execute(f"SELECT MAX(rowid) FROM {schema_sql}.{table_sql}").fetchone()
        return max(0, int(row[0])) if row and row[0] is not None else 0
    except Exception:
        row = conn.execute(f"SELECT 1 FROM {schema_sql}.{table_sql} LIMIT 1").fetchone()
        return 1 if row else 0


def _float_or_zero(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _legacy_position_state(row: dict) -> str:
    return str(row.get("phase") or row.get("state") or "").strip().lower()


def _is_nonterminal_legacy_position(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    if not (row.get("trade_id") or row.get("market_id") or row.get("condition_id")):
        return False
    state = _legacy_position_state(row)
    return state not in _TERMINAL_LEGACY_POSITION_STATES


def _legacy_positions_artifact_summary(position_view: dict) -> dict:
    """Summarize legacy positions.json without promoting it to portfolio truth."""
    path = LEGACY_POSITIONS_PATH
    summary = {
        "path": str(path),
        "exists": path.exists(),
        "authority": "legacy_json_derived_observability_only",
        "canonical_truth_source": "position_current",
        "canonical_db_status": str(position_view.get("status") or "unknown"),
        "canonical_db_open_positions": int(position_view.get("open_positions", 0) or 0),
        "status": "missing",
        "active_positions": 0,
        "active_cost_basis_usd": 0.0,
        "conflicts": [],
        "sample_positions": [],
    }
    if not path.exists():
        return summary

    try:
        stat = path.stat()
        summary["mtime"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        data, truth = read_truth_json(path)
    except Exception as exc:
        summary["status"] = "unreadable"
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)
        return summary

    summary["deprecated"] = bool(truth.get("deprecated", False))
    summary["generated_at"] = truth.get("generated_at")
    summary["stale_age_seconds"] = truth.get("stale_age_seconds")
    positions = data.get("positions", []) if isinstance(data, dict) else []
    if not isinstance(positions, list):
        summary["status"] = "malformed"
        summary["error"] = "positions is not a list"
        return summary

    active_positions = [
        row for row in positions
        if _is_nonterminal_legacy_position(row)
    ]
    summary["active_positions"] = len(active_positions)
    summary["active_cost_basis_usd"] = round(
        sum(_float_or_zero(row.get("cost_basis_usd") or row.get("size_usd")) for row in active_positions),
        4,
    )
    target_dates = sorted({
        str(row.get("target_date"))
        for row in active_positions
        if row.get("target_date")
    })
    if target_dates:
        summary["target_dates"] = target_dates
        summary["oldest_target_date"] = target_dates[0]
    chain_state_counts: dict[str, int] = {}
    for row in active_positions:
        chain_state = str(row.get("chain_state") or "unknown")
        chain_state_counts[chain_state] = chain_state_counts.get(chain_state, 0) + 1
    if chain_state_counts:
        summary["chain_state_counts"] = chain_state_counts
    summary["sample_positions"] = [
        {
            "trade_id": row.get("trade_id"),
            "city": row.get("city"),
            "target_date": row.get("target_date"),
            "bin_label": row.get("bin_label"),
            "direction": row.get("direction"),
            "state": _legacy_position_state(row) or None,
            "strategy_key": row.get("strategy_key") or row.get("strategy"),
            "chain_state": row.get("chain_state"),
            "cost_basis_usd": round(_float_or_zero(row.get("cost_basis_usd") or row.get("size_usd")), 4),
        }
        for row in active_positions[:10]
    ]
    if active_positions:
        summary["status"] = "active_legacy_positions"
    else:
        summary["status"] = "empty"

    canonical_status = str(position_view.get("status") or "").strip().lower()
    canonical_open = int(position_view.get("open_positions", 0) or 0)
    if canonical_status in {"ok", "empty"} and canonical_open == 0 and active_positions:
        summary["conflicts"].append("canonical_empty_legacy_active_positions")
        summary["status"] = "conflict"
    if summary.get("deprecated") is True and active_positions:
        summary["conflicts"].append("deprecated_legacy_file_has_active_positions")
    return summary


def _get_v2_row_counts(conn) -> dict[str, int]:
    """Query row counts for the 5 v2 tables.

    S5 R11 P10B: meta-immune-system sensor. Returns 0 for tables that don't
    exist yet (Golden Window: v2 tables are empty until data lift). Missing
    table → 0, not an error, so this never blocks status writes.
    """
    counts: dict[str, int] = {}
    attached_schemas = _attached_schema_names(conn)
    for table in _V2_TABLES:
        counts[table] = 0
        for schema in _V2_ROW_COUNT_SCHEMA_PREFERENCE.get(table, ("main",)):
            if schema not in attached_schemas or not _table_exists(conn, schema, table):
                continue
            try:
                counts[table] = _bounded_table_row_count(conn, schema, table)
            except Exception:
                counts[table] = 0
            break
    return counts


def _capability_component(
    component: str,
    *,
    allowed: bool | None,
    reason: str,
    details: dict | None = None,
) -> dict:
    return {
        "component": component,
        "allowed": allowed,
        "reason": reason,
        "details": dict(details or {}),
    }


def _safe_component(component: str, loader) -> dict:
    try:
        return loader()
    except Exception as exc:
        return _capability_component(
            component,
            allowed=False,
            reason="summary_unavailable",
            details={
                "error_type": type(exc).__name__,
                "error": str(exc),
                # Sentinel fields so downstream component builders (e.g. _collateral_component)
                # can distinguish "loader failed / DB locked" from "genuinely unconfigured".
                "configured": False,
                "authority_tier": "DEGRADED",
                "loader_failed": True,
            },
        )


def _propagate_loader_failure(payload: dict, details: dict) -> dict:
    """Copy _safe_component sentinels into a component's details dict.

    When _safe_component caught a loader exception, the payload it returned
    carries loader_failed/error_type/error sentinels. Component builders
    that construct their own details dict would otherwise drop these,
    making loader failures invisible to operators.
    """
    if isinstance(payload, dict) and payload.get("loader_failed"):
        details["loader_failed"] = True
        if "error_type" in payload:
            details["error_type"] = payload["error_type"]
        if "error" in payload:
            details["error"] = payload["error"]
    return details


def _cutover_summary() -> dict:
    from src.control.cutover_guard import summary

    return summary()


def _heartbeat_summary() -> dict:
    from src.control.heartbeat_supervisor import summary

    return summary()


def _ws_gap_summary() -> dict:
    from src.control.ws_gap_guard import summary

    return summary()


def _risk_allocator_summary() -> dict:
    from src.risk_allocator import summary

    return summary()


def _collateral_summary() -> dict:
    from src.state.collateral_ledger import get_global_ledger

    ledger = get_global_ledger()
    if ledger is None:
        return {
            "configured": False,
            "authority_tier": "UNCONFIGURED",
            "reason": "collateral_ledger_unconfigured",
        }
    snapshot = ledger.snapshot()
    payload = snapshot.to_dict()
    payload["configured"] = True
    payload["reason"] = "ok" if payload.get("authority_tier") != "DEGRADED" else "collateral_snapshot_degraded"
    return payload


def _cutover_component(action: str, payload: dict) -> dict:
    key = "redemption" if action == "redeem" else action
    decision = payload.get(key, {}) if isinstance(payload, dict) else {}
    allow_key = {
        "entry": "allow_submit",
        "exit": "allow_submit",
        "cancel": "allow_cancel",
        "redeem": "allow_redemption",
    }[action]
    allowed = bool(decision.get(allow_key, False))
    details = {
        "state": payload.get("state"),
        "allow_key": allow_key,
    }
    _propagate_loader_failure(payload, details)
    return _capability_component(
        "cutover_guard",
        allowed=allowed,
        reason=str(decision.get("block_reason") or ("allowed" if allowed else "blocked")),
        details=details,
    )


def _heartbeat_component(payload: dict, *, order_type: str = "GTC") -> dict:
    entry = payload.get("entry", {}) if isinstance(payload, dict) else {}
    allowed = bool(entry.get("allow_submit", False))
    details = {
        "health": payload.get("health"),
        "order_type": order_type,
        "required_order_types": list(entry.get("required_order_types", []) or []),
    }
    _propagate_loader_failure(payload, details)
    return _capability_component(
        "heartbeat_supervisor",
        allowed=allowed,
        reason="allowed" if allowed else str(payload.get("last_error") or payload.get("health") or "blocked"),
        details=details,
    )


def _ws_gap_component(payload: dict, *, current_executor_blocks_exit: bool = False) -> dict:
    entry = payload.get("entry", {}) if isinstance(payload, dict) else {}
    allowed = bool(entry.get("allow_submit", False))
    details = {
        "connected": payload.get("connected"),
        "subscription_state": payload.get("subscription_state"),
        "m5_reconcile_required": payload.get("m5_reconcile_required"),
        "current_executor_blocks_exit": current_executor_blocks_exit,
    }
    _propagate_loader_failure(payload, details)
    return _capability_component(
        "ws_gap_guard",
        allowed=allowed,
        reason="allowed" if allowed else str(payload.get("gap_reason") or payload.get("subscription_state") or "blocked"),
        details=details,
    )


def _risk_allocator_entry_component(payload: dict) -> dict:
    entry = payload.get("entry", {}) if isinstance(payload, dict) else {}
    allowed = bool(entry.get("allow_submit", False))
    details = {
        "configured": bool(payload.get("configured", False)),
        "kill_switch_reason": payload.get("kill_switch_reason"),
        "reduce_only": bool(payload.get("reduce_only", False)),
    }
    _propagate_loader_failure(payload, details)
    return _capability_component(
        "risk_allocator_global",
        allowed=allowed,
        reason=str(entry.get("reason") or ("allowed" if allowed else "blocked")),
        details=details,
    )


def _risk_allocator_reduce_only_component(payload: dict) -> dict:
    configured = bool(payload.get("configured", False)) if isinstance(payload, dict) else False
    kill_reason = payload.get("kill_switch_reason") if isinstance(payload, dict) else "summary_unavailable"
    allowed = configured and not kill_reason
    details = {
        "configured": configured,
        "reduce_only_submit": True,
        "reduce_only_mode": bool(payload.get("reduce_only", False)) if isinstance(payload, dict) else False,
    }
    _propagate_loader_failure(payload, details)
    return _capability_component(
        "risk_allocator_global",
        allowed=allowed,
        reason=str(kill_reason or "allowed"),
        details=details,
    )


def _collateral_component(payload: dict, *, collateral: str) -> dict:
    configured = bool(payload.get("configured", False)) if isinstance(payload, dict) else False
    authority_tier = str(payload.get("authority_tier") or "UNKNOWN")
    allowed = configured and authority_tier != "DEGRADED"
    details: dict = {
        "collateral": collateral,
        "configured": configured,
        "authority_tier": authority_tier,
        "captured_at": payload.get("captured_at"),
    }
    # Propagate loader-failure fields set by _safe_component so operators can
    # distinguish "DB locked at load time" from "genuinely unconfigured".
    if isinstance(payload, dict) and payload.get("loader_failed"):
        details["loader_failed"] = True
        if "error_type" in payload:
            details["error_type"] = payload["error_type"]
        if "error" in payload:
            details["error"] = payload["error"]
    return _capability_component(
        "collateral_ledger_global",
        allowed=allowed,
        reason=str(payload.get("reason") or ("allowed" if allowed else "blocked")),
        details=details,
    )


def _requires_intent_component(component: str, reason: str, *, details: dict | None = None) -> dict:
    return _capability_component(component, allowed=None, reason=reason, details=details)


def _action_capability(
    action: str,
    *,
    gate_key: str,
    components: list[dict],
    required_intent_components: list[dict] | None = None,
) -> dict:
    unresolved = list(required_intent_components or [])
    blocked = [c for c in components if c.get("allowed") is False]
    status = "blocked" if blocked else ("requires_intent" if unresolved else "ready")
    return {
        "action": action,
        "status": status,
        gate_key: not blocked,
        "live_action_authorized": False,
        "authority": "derived_operator_visibility",
        "components": components,
        "required_intent_components": unresolved,
        "blocked_components": [str(c.get("component")) for c in blocked],
    }


def _get_execution_capability_status() -> dict:
    """Derived operator matrix for execution gates.

    This is not a decision surface. It summarizes global gate readiness and
    explicitly leaves per-intent facts (snapshot freshness, exact notional,
    token inventory, replacement-sell policy) unresolved.
    """

    cutover = _safe_component(
        "cutover_guard",
        lambda: _capability_component("cutover_guard_summary", allowed=True, reason="loaded", details=_cutover_summary()),
    )["details"]
    heartbeat = _safe_component(
        "heartbeat_supervisor",
        lambda: _capability_component("heartbeat_summary", allowed=True, reason="loaded", details=_heartbeat_summary()),
    )["details"]
    ws_gap = _safe_component(
        "ws_gap_guard",
        lambda: _capability_component("ws_gap_summary", allowed=True, reason="loaded", details=_ws_gap_summary()),
    )["details"]
    risk = _safe_component(
        "risk_allocator",
        lambda: _capability_component("risk_allocator_summary", allowed=True, reason="loaded", details=_risk_allocator_summary()),
    )["details"]
    collateral = _safe_component(
        "collateral_ledger",
        lambda: _capability_component("collateral_summary", allowed=True, reason="loaded", details=_collateral_summary()),
    )["details"]

    entry = _action_capability(
        "entry",
        gate_key="global_allow_submit",
        components=[
            _cutover_component("entry", cutover),
            _heartbeat_component(heartbeat),
            _ws_gap_component(ws_gap),
            _risk_allocator_entry_component(risk),
            _collateral_component(collateral, collateral="pUSD"),
        ],
        required_intent_components=[
            _requires_intent_component("risk_allocator_capacity", "requires_market_notional_and_family"),
            _requires_intent_component("collateral_buy_amount", "requires_order_size_and_limit_price"),
            _requires_intent_component("executable_snapshot_gate", "requires_snapshot_id_price_size_and_token"),
        ],
    )
    exit_ = _action_capability(
        "exit",
        gate_key="global_allow_submit",
        components=[
            _cutover_component("exit", cutover),
            _heartbeat_component(heartbeat),
            _ws_gap_component(ws_gap, current_executor_blocks_exit=True),
            _risk_allocator_reduce_only_component(risk),
            _collateral_component(collateral, collateral="CTF"),
        ],
        required_intent_components=[
            _requires_intent_component("collateral_sell_inventory", "requires_token_id_and_shares"),
            _requires_intent_component("replacement_sell_guard", "requires_current_order_and_replace_context"),
            _requires_intent_component("executable_snapshot_gate", "requires_snapshot_id_price_size_and_token"),
        ],
    )
    cancel = _action_capability(
        "cancel",
        gate_key="global_allow_cancel",
        components=[_cutover_component("cancel", cutover)],
        required_intent_components=[
            _requires_intent_component("cancel_command_identity", "requires_command_id_and_venue_order_id"),
            _requires_intent_component("venue_order_cancelability", "requires_current_order_state"),
        ],
    )
    redeem = _action_capability(
        "redeem",
        gate_key="global_allow_redeem",
        components=[_cutover_component("redeem", cutover)],
        required_intent_components=[
            _requires_intent_component("payout_asset_fx_classification", "requires_redeem_command_payout_asset"),
        ],
    )
    return {
        "schema_version": 1,
        "authority": "derived_operator_visibility",
        "derived_only": True,
        "live_action_authorized": False,
        "entry": entry,
        "exit": exit_,
        "cancel": cancel,
        "redeem": redeem,
    }


def write_status(cycle_summary: dict = None) -> None:
    """Write 5-section health snapshot."""
    generated_at = datetime.now(timezone.utc).isoformat()
    risk_details = _get_risk_details()
    riskguard_level = _get_risk_level()
    cycle_summary_from_prior = cycle_summary is None
    if cycle_summary is None and STATUS_PATH.exists():
        try:
            with open(STATUS_PATH) as f:
                prior = json.load(f)
            cycle_summary = prior.get("cycle", {})
        except Exception:
            cycle_summary = {}
    recommended_strategy_gates = set(risk_details.get("recommended_strategy_gates", []) or [])
    recommended_strategy_gate_reasons = {
        str(strategy): list(reasons)
        for strategy, reasons in (risk_details.get("recommended_strategy_gate_reasons", {}) or {}).items()
        if isinstance(reasons, list)
    }
    current_entries_paused = is_entries_paused()
    if cycle_summary_from_prior:
        cycle_summary = dict(cycle_summary or {})
        if current_entries_paused:
            cycle_summary["entries_paused"] = True
            cycle_summary.pop("entries_pause_reason", None)
            cycle_summary["entries_blocked_reason"] = "entries_paused"
        else:
            cycle_summary.pop("entries_paused", None)
            cycle_summary.pop("entries_pause_reason", None)
            if cycle_summary.get("entries_blocked_reason") == "entries_paused":
                cycle_summary.pop("entries_blocked_reason", None)
    current_strategy_gates = strategy_gates()
    recommended_but_not_gated = sorted(
        strategy for strategy in recommended_strategy_gates
        if not (d := current_strategy_gates.get(strategy)) or d.enabled
    )
    gated_but_not_recommended = sorted(
        strategy for strategy, decision in current_strategy_gates.items()
        if not decision.enabled and strategy not in recommended_strategy_gates
    )
    review_required_gate_recommendations = [
        {
            "command": "set_strategy_gate",
            "strategy": strategy,
            "enabled": True,
            "note": f"recommended_by=reason_refuted:{decision.reason_code.value}",
        }
        for strategy, decision in current_strategy_gates.items()
        if not decision.enabled and reason_refuted(decision, current_data={})
    ]
    recommended_controls = list(risk_details.get("recommended_controls", []))
    recommended_control_reasons = {
        str(control): list(reasons)
        for control, reasons in (risk_details.get("recommended_control_reasons", {}) or {}).items()
        if isinstance(reasons, list)
    }
    recommended_controls_not_applied: list[str] = []
    if "tighten_risk" in recommended_controls and get_edge_threshold_multiplier() <= 1.0:
        recommended_controls_not_applied.append("tighten_risk")
    if "review_strategy_gates" in recommended_controls and recommended_but_not_gated:
        recommended_controls_not_applied.append("review_strategy_gates")
    conn = None
    try:
        conn = get_trade_connection_with_world()
        position_view = query_position_current_status_view(conn)
        strategy_health = query_strategy_health_snapshot(conn, now=generated_at)
    except Exception:
        position_view = {
            "status": "query_error",
            "positions": [],
            "open_positions": 0,
            "total_exposure_usd": 0.0,
            "unrealized_pnl": 0.0,
            "strategy_open_counts": {},
            "chain_state_counts": {},
            "exit_state_counts": {},
            "unverified_entries": 0,
            "day0_positions": 0,
        }
        strategy_health = {
            "status": "query_error",
            "by_strategy": {},
            "stale_strategy_keys": [],
        }

    # S5 R11 P10B: v2 row-count sensor
    v2_row_counts: dict[str, int] = {}
    try:
        if conn is not None:
            v2_row_counts = _get_v2_row_counts(conn)
    except Exception:
        pass

    strategy_summary: dict[str, dict] = {}
    strategy_open_counts = position_view.get("strategy_open_counts", {})
    for name, row in (strategy_health.get("by_strategy", {}) or {}).items():
        bucket = strategy_summary.setdefault(
            name,
            {
                "open_positions": int(strategy_open_counts.get(name, 0)),
                "open_exposure_usd": round(float(row.get("open_exposure_usd") or 0.0), 2),
                "realized_pnl": round(float(row.get("realized_pnl_30d") or 0.0), 2),
                "unrealized_pnl": round(float(row.get("unrealized_pnl") or 0.0), 2),
            },
        )
        bucket["total_pnl"] = round(bucket["realized_pnl"] + bucket["unrealized_pnl"], 2)
        bucket["settlement_count"] = int(row.get("settled_trades_30d") or 0)
        bucket["settlement_pnl"] = round(float(row.get("realized_pnl_30d") or 0.0), 2)
        bucket["settlement_accuracy"] = row.get("win_rate_30d")
        bucket["settlement_source"] = "strategy_health"
        bucket["settlement_window"] = "30d"
        bucket["fill_rate_14d"] = row.get("fill_rate_14d")
        bucket["execution_decay_flag"] = bool(row.get("execution_decay_flag", 0))
        bucket["edge_compression_flag"] = bool(row.get("edge_compression_flag", 0))
    for name, open_count in strategy_open_counts.items():
        bucket = strategy_summary.setdefault(
            name,
            {
                "open_positions": int(open_count),
                "open_exposure_usd": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "total_pnl": 0.0,
                "settlement_count": 0,
                "settlement_pnl": 0.0,
                "settlement_accuracy": None,
                "settlement_source": "strategy_health",
                "settlement_window": "30d",
            },
        )
        bucket["open_positions"] = int(open_count)
    for name, bucket in strategy_summary.items():
        _gate = current_strategy_gates.get(name)
        bucket["gated"] = _gate is not None and not _gate.enabled
        bucket["recommended_gate"] = name in recommended_strategy_gates
        bucket["recommended_gate_reasons"] = list(recommended_strategy_gate_reasons.get(name, []))

    status = {
        "timestamp": generated_at,
        "process": {
            "pid": os.getpid(),
            "mode": get_mode(),
            "version": "zeus_v2",
        },
        "control": {
            "entries_paused": current_entries_paused,
            "entries_pause_source": get_entries_pause_source(),
            "entries_pause_reason": get_entries_pause_reason(),
            "edge_threshold_multiplier": get_edge_threshold_multiplier(),
            "strategy_gates": {k: v.to_dict() for k, v in current_strategy_gates.items()},
            "recommended_controls": recommended_controls,
            "recommended_control_reasons": recommended_control_reasons,
            "recommended_strategy_gates": risk_details.get("recommended_strategy_gates", []),
            "recommended_strategy_gate_reasons": recommended_strategy_gate_reasons,
            "recommended_but_not_gated": recommended_but_not_gated,
            "gated_but_not_recommended": gated_but_not_recommended,
            "recommended_controls_not_applied": recommended_controls_not_applied,
            "review_required_gate_recommendations": review_required_gate_recommendations,
        },
        "risk": {
            "level": riskguard_level,
            "riskguard_level": riskguard_level,
            "details": risk_details,
        },
        "portfolio": {
            "open_positions": int(position_view.get("open_positions", 0)),
            "total_exposure_usd": round(float(position_view.get("total_exposure_usd", 0.0) or 0.0), 2),
            "heat_pct": 0.0,
            "initial_bankroll": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": round(float(position_view.get("unrealized_pnl", 0.0) or 0.0), 2),
            "total_pnl": 0.0,
            "effective_bankroll": 0.0,
            "bankroll": 0.0,
            "positions": list(position_view.get("positions", [])),
        },
        "runtime": {
            "chain_state_counts": dict(position_view.get("chain_state_counts", {})),
            "exit_state_counts": dict(position_view.get("exit_state_counts", {})),
            "unverified_entries": int(position_view.get("unverified_entries", 0)),
            "day0_positions": int(position_view.get("day0_positions", 0)),
        },
        "strategy": strategy_summary,
        "execution": {
            "fdr_family_size": int((cycle_summary or {}).get("fdr_family_size", 0)),
            "fdr_fallback_fired": bool((cycle_summary or {}).get("fdr_fallback_fired", False)),
        },
        "execution_capability": _get_execution_capability_status(),
        "calibration_serving": {},
        "price_evidence": {},
        "learning": {},
        "lifecycle_funnel": {},
        "no_trade": {},
        "cycle": cycle_summary or {},
        # S5 R11 P10B: v2 row-count observability sensor
        "v2_row_counts": v2_row_counts,
        # S5 R11 P10B: dual-track scaffold claim (True since P9C closed the main line)
        "dual_track_scaffold_claimed": True,
        "discrepancy_flags": [],
    }
    legacy_positions_artifact = _legacy_positions_artifact_summary(position_view)
    status["portfolio"]["legacy_artifact"] = legacy_positions_artifact
    status["control"]["recommended_auto_commands"] = recommended_autosafe_commands_from_status(status)
    status["control"]["review_required_commands"] = review_required_commands_from_status(status)
    status["control"]["recommended_commands"] = recommended_commands_from_status(
        status,
        include_review_required=True,
    )
    risk_effective_bankroll = _round_money_or_none(risk_details.get("effective_bankroll"))
    risk_initial_bankroll = _round_money_or_none(risk_details.get("initial_bankroll"))
    realized_pnl = risk_details.get("realized_pnl")
    unrealized_pnl = risk_details.get("unrealized_pnl")
    total_pnl = risk_details.get("total_pnl")
    bankroll_truth = risk_details.get("bankroll_truth") if isinstance(risk_details.get("bankroll_truth"), dict) else {}
    risk_bankroll_truth_source = risk_details.get("bankroll_truth_source") or bankroll_truth.get("source")
    risk_bankroll_truth_authority = bankroll_truth.get("authority")
    risk_bankroll_provenance_ok = (
        risk_effective_bankroll is not None
        and risk_initial_bankroll is not None
        and str(risk_bankroll_truth_source or "") == "polymarket_wallet"
        and str(bankroll_truth.get("source") or "") == "polymarket_wallet"
        and str(risk_bankroll_truth_authority or "") == "canonical"
    )
    effective_bankroll = risk_effective_bankroll if risk_bankroll_provenance_ok else None
    initial_bankroll = risk_initial_bankroll if risk_bankroll_provenance_ok else None
    bankroll_truth_source = risk_bankroll_truth_source if risk_bankroll_provenance_ok else None
    bankroll_truth_authority = risk_bankroll_truth_authority if risk_bankroll_provenance_ok else None
    bankroll_truth_status = "present" if risk_bankroll_provenance_ok else "missing"
    bankroll_derivation = "riskguard_effective_bankroll" if risk_bankroll_provenance_ok else None
    bankroll_fallback_source = None
    bankroll_rejected_source = None
    if risk_effective_bankroll is not None and not risk_bankroll_provenance_ok:
        bankroll_rejected_source = "riskguard_unproven"
    if realized_pnl is None:
        realized_pnl = round(
            sum(float(bucket.get("realized_pnl", 0.0) or 0.0) for bucket in strategy_summary.values()),
            2,
        )
    if unrealized_pnl is None:
        unrealized_pnl = status["portfolio"]["unrealized_pnl"]
    if total_pnl is None:
        total_pnl = round(float(realized_pnl or 0.0) + float(unrealized_pnl or 0.0), 2)
    if initial_bankroll is None:
        initial_bankroll = _round_money_or_none((cycle_summary or {}).get("wallet_balance_usd"))
        if initial_bankroll is not None:
            bankroll_truth_source = "cycle_summary.wallet_balance_usd"
            bankroll_truth_authority = "runtime_summary"
            bankroll_fallback_source = "cycle_summary.wallet_balance_usd"
    if initial_bankroll is None:
        # Removed 2026-05-04: previously fell back to retired config-literal
        # capital. Now query the on-chain wallet via bankroll_provider;
        # if the provider has no usable value (None / wallet unreachable + no
        # cache), leave initial_bankroll as None so downstream renders it as
        # null + flags DATA_DEGRADED rather than smuggling a config literal.
        try:
            from src.runtime.bankroll_provider import current as _bankroll_current
            _record = _bankroll_current()
        except Exception:
            _record = None
        if _record is not None:
            initial_bankroll = round(float(_record.value_usd), 2)
            bankroll_truth_source = str(getattr(_record, "source", None) or "polymarket_wallet")
            bankroll_truth_authority = str(getattr(_record, "authority", None) or "canonical")
            bankroll_fallback_source = "bankroll_provider"
        else:
            bankroll_fallback_source = "bankroll_provider_unavailable"
    if effective_bankroll is None:
        if initial_bankroll is not None:
            # Definition A: status bankroll preserves wallet-equity identity.
            # PnL is report analytics; folding it into bankroll would recreate
            # the legacy wallet+PnL synthetic equity object.
            effective_bankroll = round(float(initial_bankroll), 2)
            bankroll_truth_status = "present"
            bankroll_derivation = "wallet_equity_no_pnl"
        else:
            effective_bankroll = None
            bankroll_truth_status = "missing"
            bankroll_derivation = "missing_wallet_truth"
    status["portfolio"]["realized_pnl"] = round(float(realized_pnl or 0.0), 2)
    status["portfolio"]["unrealized_pnl"] = round(float(unrealized_pnl or 0.0), 2)
    status["portfolio"]["total_pnl"] = round(float(total_pnl or 0.0), 2)
    status["portfolio"]["effective_bankroll"] = _round_money_or_none(effective_bankroll)
    status["portfolio"]["bankroll"] = _round_money_or_none(effective_bankroll)
    status["portfolio"]["initial_bankroll"] = _round_money_or_none(initial_bankroll)
    status["portfolio"]["bankroll_object_identity"] = "wallet_equity"
    status["portfolio"]["bankroll_truth_status"] = bankroll_truth_status
    status["portfolio"]["bankroll_truth_source"] = bankroll_truth_source or "unknown"
    status["portfolio"]["bankroll_truth_authority"] = bankroll_truth_authority or "unknown"
    status["portfolio"]["effective_bankroll_derivation"] = bankroll_derivation
    if bankroll_rejected_source is not None:
        status["portfolio"]["bankroll_rejected_source"] = bankroll_rejected_source
    if effective_bankroll is not None and float(effective_bankroll) > 0:
        status["portfolio"]["heat_pct"] = round(
            (float(status["portfolio"]["total_exposure_usd"]) / float(effective_bankroll)) * 100,
            1,
        )
    try:
        current_regime_started_at = str(
            ((risk_details.get("strategy_tracker_accounting") or {}).get("current_regime_started_at")) or ""
        )
        status["execution"] = query_execution_event_summary(
            conn,
            not_before=current_regime_started_at or None,
        )
        status["learning"] = query_learning_surface_summary(
            conn,
            not_before=current_regime_started_at or None,
        )
        status["calibration_serving"] = build_calibration_serving_status(conn)
        status["lifecycle_funnel"] = query_lifecycle_funnel_report(
            conn,
            not_before=current_regime_started_at or None,
        )
        status["price_evidence"] = build_price_evidence_report(conn)
        recent_no_trades = query_no_trade_cases(conn, hours=24)
        stage_counts: dict[str, int] = {}
        for case in recent_no_trades:
            stage = str(case.get("rejection_stage") or "UNKNOWN")
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
        status["no_trade"] = {
            "recent_stage_counts": stage_counts,
        }
    except Exception:
        status["execution"] = {"error": "execution_summary_unavailable"}
        status["learning"] = {"error": "learning_summary_unavailable"}
        status["calibration_serving"] = {
            "status": "query_error",
            "authority": "derived_operator_visibility",
            "error": "calibration_serving_summary_unavailable",
        }
        status["lifecycle_funnel"] = {
            "status": "query_error",
            "authority": "derived_operator_visibility",
            "error": "lifecycle_funnel_summary_unavailable",
        }
        status["price_evidence"] = build_price_evidence_error_report(
            "status_summary",
            "price_evidence_summary_unavailable",
        )
        status["no_trade"] = {"error": "no_trade_summary_unavailable"}
    finally:
        if conn is not None:
            conn.close()

    # S5 R11 P10B: discrepancy flag — claim=True AND any v2 table has 0 rows
    if status.get("dual_track_scaffold_claimed") and v2_row_counts:
        empty_v2 = [t for t, c in v2_row_counts.items() if c == 0]
        if empty_v2:
            status["discrepancy_flags"].append("v2_empty_despite_closure_claim")

    consistency_issues: list[str] = []
    cycle_risk_level = str((cycle_summary or {}).get("risk_level") or "")
    if cycle_risk_level and cycle_risk_level != riskguard_level:
        consistency_issues.append(
            f"cycle_risk_level_mismatch:{cycle_risk_level}->{riskguard_level}"
        )
    if bool((cycle_summary or {}).get("failed", False)):
        consistency_issues.append("cycle_failed")
    if status.get("execution", {}).get("error"):
        consistency_issues.append("execution_summary_unavailable")
    if status.get("learning", {}).get("error"):
        consistency_issues.append("learning_summary_unavailable")
    calibration_serving_status = str((status.get("calibration_serving", {}) or {}).get("status") or "")
    if calibration_serving_status == "query_error":
        consistency_issues.append("calibration_serving_summary_unavailable")
    elif calibration_serving_status == "partial":
        consistency_issues.append("calibration_serving_summary_partial")
    lifecycle_funnel_status = str((status.get("lifecycle_funnel", {}) or {}).get("status") or "")
    if lifecycle_funnel_status == "query_error":
        consistency_issues.append("lifecycle_funnel_summary_unavailable")
    elif lifecycle_funnel_status == "partial":
        consistency_issues.append("lifecycle_funnel_summary_partial")
    price_evidence_status = str((status.get("price_evidence", {}) or {}).get("status") or "")
    if price_evidence_status == "query_error":
        consistency_issues.append("price_evidence_summary_unavailable")
    elif price_evidence_status == "partial":
        consistency_issues.append("price_evidence_summary_partial")
    if status.get("no_trade", {}).get("error"):
        consistency_issues.append("no_trade_summary_unavailable")
    monitor_chain_missing = int((cycle_summary or {}).get("monitor_chain_missing", 0) or 0)
    if monitor_chain_missing > 0:
        consistency_issues.append(f"cycle_monitor_chain_missing:{monitor_chain_missing}")
    if position_view.get("status") != "ok":
        consistency_issues.append(f"position_current_{position_view.get('status')}")
    if "canonical_empty_legacy_active_positions" in set(legacy_positions_artifact.get("conflicts", [])):
        consistency_issues.append("legacy_positions_json_conflicts_with_canonical_empty")
    strategy_health_status = str(strategy_health.get("status") or "")
    if strategy_health_status not in {"fresh"}:
        consistency_issues.append(f"strategy_health_{strategy_health_status or 'unknown'}")
    if status["portfolio"].get("bankroll_truth_status") == "missing":
        consistency_issues.append("bankroll_truth_missing")
    if status["portfolio"].get("bankroll_rejected_source"):
        consistency_issues.append(f"bankroll_rejected_{status['portfolio']['bankroll_rejected_source']}")

    status["risk"]["consistency_check"] = {
        "ok": not consistency_issues,
        "issues": consistency_issues,
        "cycle_risk_level": cycle_risk_level or None,
    }
    # K4: infrastructure / data-availability issues are a SEPARATE dimension from
    # trading risk. Previously any consistency_issue escalated risk.level to RED,
    # which meant cold-start states like strategy_health_empty or
    # cycle_risk_level_mismatch produced false-RED alerts indistinguishable from
    # real trading halts. risk.level now reflects RiskGuard's six trading
    # dimensions only. infrastructure_level reflects observability/data-health.
    # Downstream consumers (Venus supervisor, daily review, Discord alerts) must
    # read both fields and treat them as orthogonal signals.
    if not consistency_issues:
        infrastructure_level = "GREEN"
    else:
        # Hard infrastructure failures escalate to RED because they mean the
        # observability layer cannot be trusted; soft cold-start or
        # availability states stay YELLOW so they do not page as emergencies.
        _HARD_INFRASTRUCTURE_FAILURE_PREFIXES = (
            "cycle_failed",
            "execution_summary_unavailable",
            "learning_summary_unavailable",
            "no_trade_summary_unavailable",
            "cycle_monitor_chain_missing",
            "legacy_positions_json_conflicts_with_canonical_empty",
            "position_current_missing_table",
            "position_current_query_error",
        )
        if any(
            issue.startswith(prefix)
            for issue in consistency_issues
            for prefix in _HARD_INFRASTRUCTURE_FAILURE_PREFIXES
        ):
            infrastructure_level = "RED"
        else:
            infrastructure_level = "YELLOW"
    status["risk"]["infrastructure_level"] = infrastructure_level
    status["risk"]["infrastructure_issues"] = list(consistency_issues)

    learning_by_strategy = (status.get("learning", {}) or {}).get("by_strategy", {}) or {}
    for name, learning_bucket in learning_by_strategy.items():
        _lgate = current_strategy_gates.get(name)
        bucket = strategy_summary.setdefault(
            name,
            {
                "open_positions": 0,
                "open_exposure_usd": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "total_pnl": 0.0,
                "gated": _lgate is not None and not _lgate.enabled,
                "recommended_gate": name in recommended_strategy_gates,
                "recommended_gate_reasons": list(recommended_strategy_gate_reasons.get(name, [])),
                "settlement_count": 0,
                "settlement_pnl": 0.0,
                "settlement_accuracy": None,
                "settlement_source": "strategy_health",
                "settlement_window": "30d",
            },
        )
        bucket.setdefault("settlement_count", 0)
        bucket.setdefault("settlement_pnl", 0.0)
        bucket.setdefault("settlement_accuracy", None)
        bucket.setdefault("settlement_source", "strategy_health")
        bucket.setdefault("settlement_window", "30d")
        bucket["learning_settlement_count"] = learning_bucket.get("settlement_count", 0)
        bucket["learning_settlement_pnl"] = learning_bucket.get("settlement_pnl", 0.0)
        bucket["learning_settlement_accuracy"] = learning_bucket.get("settlement_accuracy")
        bucket["learning_settlement_source"] = "learning_surface"
        bucket["learning_settlement_window"] = (
            "current_regime" if current_regime_started_at else "learning_surface_default"
        )
        bucket["no_trade_count"] = learning_bucket.get("no_trade_count", 0)
        bucket["no_trade_stage_counts"] = dict(learning_bucket.get("no_trade_stage_counts", {}) or {})
        bucket["entry_attempted"] = learning_bucket.get("entry_attempted", 0)
        bucket["entry_filled"] = learning_bucket.get("entry_filled", 0)
        bucket["entry_rejected"] = learning_bucket.get("entry_rejected", 0)
    status = annotate_truth_payload(status, STATUS_PATH, generated_at=generated_at, authority="VERIFIED")
    status["truth"]["db_primary_inputs"] = {
        "position_current": str(position_view.get("status") or "unknown"),
        "strategy_health": strategy_health_status or "unknown",
    }
    compatibility_inputs: dict[str, object] = {}
    if current_regime_started_at:
        compatibility_inputs["strategy_tracker_current_regime_started_at"] = current_regime_started_at
    if bankroll_fallback_source is not None:
        # Removed 2026-05-04: the previous config-cap fallback label is gone.
        # Live truth now flows from bankroll_provider.current(); when it returns None the field stays
        # null and DATA_DEGRADED surfaces upstream — no config-literal smuggle.
        compatibility_inputs["bankroll_fallback_source"] = bankroll_fallback_source
    if bankroll_rejected_source is not None:
        compatibility_inputs["bankroll_rejected_source"] = bankroll_rejected_source
    if compatibility_inputs:
        status["truth"]["compatibility_inputs"] = compatibility_inputs

    # Atomic write
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(STATUS_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(status, f, indent=2)
        os.replace(tmp, str(STATUS_PATH))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
