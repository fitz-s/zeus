"""Status summary: written every cycle. Zeus is not a black box.

Blueprint v2 §10: 5-section health snapshot.
Written to the mode-qualified truth file for Venus/OpenClaw to read.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from src.config import STATE_DIR, settings, state_path
from src.control.control_plane import get_edge_threshold_multiplier, is_entries_paused, strategy_gates
from src.state.decision_chain import query_learning_surface_summary
from src.state.db import get_connection, query_execution_event_summary
from src.state.decision_chain import query_no_trade_cases
from src.state.portfolio import ADMIN_EXITS, PortfolioState, load_portfolio, portfolio_heat
from src.state.truth_files import annotate_truth_payload

logger = logging.getLogger(__name__)

STATUS_PATH = state_path("status_summary.json")


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
    portfolio = load_portfolio()
    generated_at = datetime.now(timezone.utc).isoformat()
    risk_details = _get_risk_details()
    if cycle_summary is None and STATUS_PATH.exists():
        try:
            with open(STATUS_PATH) as f:
                prior = json.load(f)
            cycle_summary = prior.get("cycle", {})
        except Exception:
            cycle_summary = {}

    strategy_summary: dict[str, dict] = {}
    for pos in portfolio.positions:
        strategy = pos.strategy or "unclassified"
        bucket = strategy_summary.setdefault(
            strategy,
            {
                "open_positions": 0,
                "open_exposure_usd": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
            },
        )
        bucket["open_positions"] += 1
        bucket["open_exposure_usd"] += float(pos.size_usd)
        bucket["unrealized_pnl"] += float(pos.unrealized_pnl)

    for exit_row in portfolio.recent_exits:
        if exit_row.get("exit_reason") in ADMIN_EXITS:
            continue
        strategy = exit_row.get("strategy") or "unclassified"
        bucket = strategy_summary.setdefault(
            strategy,
            {
                "open_positions": 0,
                "open_exposure_usd": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
            },
        )
        bucket["realized_pnl"] += float(exit_row.get("pnl", 0.0) or 0.0)

    for bucket in strategy_summary.values():
        bucket["open_exposure_usd"] = round(bucket["open_exposure_usd"], 2)
        bucket["realized_pnl"] = round(bucket["realized_pnl"], 2)
        bucket["unrealized_pnl"] = round(bucket["unrealized_pnl"], 2)
        bucket["total_pnl"] = round(bucket["realized_pnl"] + bucket["unrealized_pnl"], 2)
    recommended_strategy_gates = set(risk_details.get("recommended_strategy_gates", []) or [])
    recommended_strategy_gate_reasons = {
        str(strategy): list(reasons)
        for strategy, reasons in (risk_details.get("recommended_strategy_gate_reasons", {}) or {}).items()
        if isinstance(reasons, list)
    }
    current_strategy_gates = strategy_gates()
    for name, bucket in strategy_summary.items():
        bucket["gated"] = not current_strategy_gates.get(name, True)
        bucket["recommended_gate"] = name in recommended_strategy_gates
        bucket["recommended_gate_reasons"] = list(recommended_strategy_gate_reasons.get(name, []))
    recommended_but_not_gated = sorted(
        strategy for strategy in recommended_strategy_gates
        if current_strategy_gates.get(strategy, True)
    )
    gated_but_not_recommended = sorted(
        strategy for strategy, enabled in current_strategy_gates.items()
        if enabled is False and strategy not in recommended_strategy_gates
    )
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

    chain_state_counts: dict[str, int] = {}
    exit_state_counts: dict[str, int] = {}
    for pos in portfolio.positions:
        chain_key = str(pos.chain_state or "unknown")
        chain_state_counts[chain_key] = chain_state_counts.get(chain_key, 0) + 1
        exit_key = str(pos.exit_state or "none")
        exit_state_counts[exit_key] = exit_state_counts.get(exit_key, 0) + 1

    status = {
        "timestamp": generated_at,
        "process": {
            "pid": os.getpid(),
            "mode": settings.mode,
            "version": "zeus_v2",
        },
        "control": {
            "entries_paused": is_entries_paused(),
            "edge_threshold_multiplier": get_edge_threshold_multiplier(),
            "strategy_gates": strategy_gates(),
            "recommended_controls": recommended_controls,
            "recommended_control_reasons": recommended_control_reasons,
            "recommended_strategy_gates": risk_details.get("recommended_strategy_gates", []),
            "recommended_strategy_gate_reasons": recommended_strategy_gate_reasons,
            "recommended_but_not_gated": recommended_but_not_gated,
            "gated_but_not_recommended": gated_but_not_recommended,
            "recommended_controls_not_applied": recommended_controls_not_applied,
        },
        "risk": {
            "level": _get_risk_level(),
            "details": risk_details,
        },
        "portfolio": {
            "open_positions": len(portfolio.positions),
            "total_exposure_usd": round(sum(p.size_usd for p in portfolio.positions), 2),
            "heat_pct": round(portfolio_heat(portfolio) * 100, 1),
            "initial_bankroll": round(portfolio.initial_bankroll, 2),
            "realized_pnl": round(portfolio.realized_pnl, 2),
            "unrealized_pnl": round(portfolio.total_unrealized_pnl, 2),
            "total_pnl": round(portfolio.total_pnl, 2),
            "effective_bankroll": round(portfolio.effective_bankroll, 2),
            "bankroll": round(portfolio.effective_bankroll, 2),
            "positions": [
                {
                    "trade_id": p.trade_id,
                    "city": p.city,
                    "direction": p.direction,
                    "strategy": p.strategy,
                    "state": p.state,
                    "chain_state": p.chain_state,
                    "exit_state": p.exit_state,
                    "entry_fill_verified": p.entry_fill_verified,
                    "admin_exit_reason": p.admin_exit_reason,
                    "size_usd": p.size_usd,
                    "shares": p.effective_shares,
                    "entry_price": p.entry_price,
                    "edge": p.edge,
                    "bin_label": p.bin_label,
                    "decision_snapshot_id": p.decision_snapshot_id,
                    "day0_entered_at": p.day0_entered_at,
                    "mark_price": p.last_monitor_market_price,
                    "unrealized_pnl": round(p.unrealized_pnl, 2),
                }
                for p in portfolio.positions
            ],
        },
        "runtime": {
            "chain_state_counts": chain_state_counts,
            "exit_state_counts": exit_state_counts,
            "unverified_entries": sum(
                1 for pos in portfolio.positions
                if pos.state == "pending_tracked" or not pos.entry_fill_verified
            ),
            "day0_positions": sum(1 for pos in portfolio.positions if pos.state == "day0_window"),
        },
        "strategy": strategy_summary,
        "execution": {},
        "learning": {},
        "no_trade": {},
        "cycle": cycle_summary or {},
    }
    try:
        conn = get_connection()
        status["execution"] = query_execution_event_summary(conn)
        status["learning"] = query_learning_surface_summary(conn)
        recent_no_trades = query_no_trade_cases(conn, hours=24)
        stage_counts: dict[str, int] = {}
        for case in recent_no_trades:
            stage = str(case.get("rejection_stage") or "UNKNOWN")
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
        status["no_trade"] = {
            "recent_stage_counts": stage_counts,
        }
        conn.close()
    except Exception:
        status["execution"] = {"error": "execution_summary_unavailable"}
        status["learning"] = {"error": "learning_summary_unavailable"}
        status["no_trade"] = {"error": "no_trade_summary_unavailable"}

    learning_by_strategy = (status.get("learning", {}) or {}).get("by_strategy", {}) or {}
    for name, learning_bucket in learning_by_strategy.items():
        bucket = strategy_summary.setdefault(
            name,
            {
                "open_positions": 0,
                "open_exposure_usd": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "total_pnl": 0.0,
                "gated": not current_strategy_gates.get(name, True),
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
    status = annotate_truth_payload(status, STATUS_PATH, mode=settings.mode, generated_at=generated_at)

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
