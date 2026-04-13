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
from src.state.decision_chain import query_learning_surface_summary
from src.state.db import (
    get_trade_connection_with_world,
    query_execution_event_summary,
    query_position_current_status_view,
    query_strategy_health_snapshot,
)
from src.state.decision_chain import query_no_trade_cases
from src.state.truth_files import annotate_truth_payload

logger = logging.getLogger(__name__)

STATUS_PATH = state_path("status_summary.json")


def _enum_text(value, default: str) -> str:
    if value in (None, ""):
        return default
    return str(getattr(value, "value", value))


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
        "learning": {},
        "no_trade": {},
        "cycle": cycle_summary or {},
    }
    status["control"]["recommended_auto_commands"] = recommended_autosafe_commands_from_status(status)
    status["control"]["review_required_commands"] = review_required_commands_from_status(status)
    status["control"]["recommended_commands"] = recommended_commands_from_status(
        status,
        include_review_required=True,
    )
    effective_bankroll = risk_details.get("effective_bankroll")
    realized_pnl = risk_details.get("realized_pnl")
    unrealized_pnl = risk_details.get("unrealized_pnl")
    total_pnl = risk_details.get("total_pnl")
    if realized_pnl is None:
        realized_pnl = round(
            sum(float(bucket.get("realized_pnl", 0.0) or 0.0) for bucket in strategy_summary.values()),
            2,
        )
    if unrealized_pnl is None:
        unrealized_pnl = status["portfolio"]["unrealized_pnl"]
    if total_pnl is None:
        total_pnl = round(float(realized_pnl or 0.0) + float(unrealized_pnl or 0.0), 2)
    initial_bankroll = risk_details.get("initial_bankroll")
    if initial_bankroll is None:
        initial_bankroll = (cycle_summary or {}).get("wallet_balance_usd")
    if initial_bankroll is None:
        initial_bankroll = round(float(settings.capital_base_usd), 2)
    if effective_bankroll is None:
        effective_bankroll = round(float(initial_bankroll or 0.0) + float(total_pnl or 0.0), 2)
    status["portfolio"]["realized_pnl"] = round(float(realized_pnl or 0.0), 2)
    status["portfolio"]["unrealized_pnl"] = round(float(unrealized_pnl or 0.0), 2)
    status["portfolio"]["total_pnl"] = round(float(total_pnl or 0.0), 2)
    status["portfolio"]["effective_bankroll"] = round(float(effective_bankroll or 0.0), 2)
    status["portfolio"]["bankroll"] = round(float(effective_bankroll or 0.0), 2)
    status["portfolio"]["initial_bankroll"] = round(float(initial_bankroll or 0.0), 2)
    if float(effective_bankroll or 0.0) > 0:
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
        status["no_trade"] = {"error": "no_trade_summary_unavailable"}
    finally:
        if conn is not None:
            conn.close()

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
    if status.get("no_trade", {}).get("error"):
        consistency_issues.append("no_trade_summary_unavailable")
    monitor_chain_missing = int((cycle_summary or {}).get("monitor_chain_missing", 0) or 0)
    if monitor_chain_missing > 0:
        consistency_issues.append(f"cycle_monitor_chain_missing:{monitor_chain_missing}")
    if position_view.get("status") != "ok":
        consistency_issues.append(f"position_current_{position_view.get('status')}")
    strategy_health_status = str(strategy_health.get("status") or "")
    if strategy_health_status not in {"fresh"}:
        consistency_issues.append(f"strategy_health_{strategy_health_status or 'unknown'}")

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
            },
        )
        bucket["settlement_count"] = learning_bucket.get("settlement_count", 0)
        bucket["settlement_pnl"] = learning_bucket.get("settlement_pnl", 0.0)
        bucket["settlement_accuracy"] = learning_bucket.get("settlement_accuracy")
        bucket["no_trade_count"] = learning_bucket.get("no_trade_count", 0)
        bucket["no_trade_stage_counts"] = dict(learning_bucket.get("no_trade_stage_counts", {}) or {})
        bucket["entry_attempted"] = learning_bucket.get("entry_attempted", 0)
        bucket["entry_filled"] = learning_bucket.get("entry_filled", 0)
        bucket["entry_rejected"] = learning_bucket.get("entry_rejected", 0)
    status = annotate_truth_payload(status, STATUS_PATH, mode=get_mode(), generated_at=generated_at)
    status["truth"]["db_primary_inputs"] = {
        "position_current": str(position_view.get("status") or "unknown"),
        "strategy_health": strategy_health_status or "unknown",
    }
    compatibility_inputs: dict[str, object] = {}
    if current_regime_started_at:
        compatibility_inputs["strategy_tracker_current_regime_started_at"] = current_regime_started_at
    if risk_details.get("initial_bankroll") is None:
        compatibility_inputs["bankroll_fallback_source"] = "settings.capital_base_usd"
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
